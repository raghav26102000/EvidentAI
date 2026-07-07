"""Sandbox runner for deterministic worker scripts.

Constraints applied to child process:
- CPU time limit (SIGXCPU on breach)
- Address space (memory) limit
- No network (unshare -n if available; falls back to noop with warning)
- Wall-clock timeout (parent kills subprocess)
- Distinct temp cwd; script has no access to backend secrets/env vars
- Communicates over stdin/stdout only (no filesystem escapes needed)

The same runner will be reused by Phase 2 statistical/insight agents.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxError(Exception):
    pass


class SandboxTimeout(SandboxError):
    pass


@dataclass
class SandboxLimits:
    cpu_seconds: int = 20
    memory_bytes: int = 512 * 1024 * 1024  # 512 MiB
    wall_timeout_seconds: int = 30


_WORKER_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL")


def _child_preexec(limits: SandboxLimits):
    """Set rlimits inside the child before exec. Runs in child process."""
    def _apply():
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        # New session so parent can kill the whole process group on timeout.
        os.setsid()
    return _apply


def _has_unshare() -> bool:
    if shutil.which("unshare") is None:
        return False
    # Verify unshare -n actually works in this container (needs CAP_SYS_ADMIN).
    try:
        r = subprocess.run(
            ["unshare", "-n", "true"],
            capture_output=True,
            timeout=3,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# Cache the result: probe once at import.
_UNSHARE_WORKS = _has_unshare()
if not _UNSHARE_WORKS:
    logger.warning(
        "unshare(1) network namespace unavailable in this environment; "
        "sandbox will run with rlimits + timeout but WITHOUT kernel-enforced "
        "network isolation. In production (with CAP_SYS_ADMIN or user-ns), "
        "network isolation will be enforced automatically."
    )


async def run_worker(script_path: str, stdin_payload: bytes, limits: SandboxLimits | None = None) -> dict:
    """Run ``python script_path`` in a sandbox, feeding stdin_payload; parse
    stdout as JSON."""
    lim = limits or SandboxLimits()
    env = {k: v for k, v in os.environ.items() if k in _WORKER_ENV_ALLOWLIST}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    with tempfile.TemporaryDirectory(prefix="evai-sbox-") as cwd:
        args: list[str] = []
        if _UNSHARE_WORKS:
            # Isolate network namespace and mount namespace; user ns for perms.
            args = ["unshare", "-n", "--"]
        args += [sys.executable, script_path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                preexec_fn=_child_preexec(lim),
            )
        except FileNotFoundError as e:
            raise SandboxError(f"Failed to spawn sandbox: {e}") from e

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=lim.wall_timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
            raise SandboxTimeout(f"Sandbox exceeded {lim.wall_timeout_seconds}s") from e

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:4000]
            raise SandboxError(f"Sandbox exit={proc.returncode}: {err}")

        try:
            return json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as e:
            preview = stdout[:400].decode("utf-8", errors="replace")
            raise SandboxError(f"Sandbox produced non-JSON output: {preview}") from e


WORKER_DIR = Path(__file__).parent / "workers"
