"""Sandboxed subprocess runner for agent-generated code.

Isolation mechanism (documented + enforced):
    Layer 1: dedicated non-root uid (evsandbox, uid=5000) via setgid+setuid
             in preexec_fn. No sudo, no login shell.
    Layer 2: prctl(PR_SET_NO_NEW_PRIVS, 1) so setuid binaries can't escalate
             AND so a non-root process can install a seccomp-bpf filter.
             Applied inside the child *before* execve.
    Layer 3: POSIX rlimits: CPU, address-space (memory), NPROC, NOFILE,
             FSIZE, CORE. Applied in preexec_fn.
    Layer 4: setsid() so the parent can kill the entire process group
             on wall-clock timeout.
    Layer 5: seccomp-bpf filter (loaded from *inside* the wrapper script
             after all imports are warm). Not managed here; see
             app/workers/stats_worker.py.
    Layer 6: env stripped to PATH/LANG/LC_ALL. cwd = ephemeral scratch dir
             chowned to evsandbox, mode 700, auto-deleted by parent.
    Layer 7: dataset payload delivered via child's stdin (fd 0); the child
             never opens the dataset by path.
    Layer 8: result JSON delivered via a dedicated pipe on child's fd 3,
             completely separate from stdout. Anything the LLM-generated
             code prints goes to stdout and is captured as 'logs' only.

The container this runs in lacks user namespaces, network namespaces, and
Landlock (all verified to return EPERM/ENOSYS at container start), so
namespaces-based sandboxes (bwrap, nsjail, gVisor, Docker-in-Docker) are
not available. The mechanism above is what is enforceable in a stock
unprivileged Kubernetes pod on this kernel.
"""
from __future__ import annotations
import asyncio
import ctypes
import ctypes.util
import json
import logging
import os
import pwd
import resource
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SANDBOX_USER = "evsandbox"
_PR_SET_NO_NEW_PRIVS = 38  # from <linux/prctl.h>


class SandboxError(Exception):
    pass


class SandboxTimeout(SandboxError):
    pass


@dataclass
class SandboxResult:
    result: Optional[dict]
    stdout_logs: str
    stderr_logs: str
    elapsed_seconds: float
    exit_code: int
    timed_out: bool
    killed_by_signal: Optional[int]


@dataclass
class SandboxLimits:
    cpu_seconds: int = 30
    memory_bytes: int = 1024 * 1024 * 1024   # 1 GiB (virtual, RLIMIT_AS)
    wall_timeout_seconds: int = 20
    fsize_bytes: int = 8 * 1024 * 1024
    nproc: int = 16
    nofile: int = 64
    # Phase 2 default: drop to unprivileged uid before execve. Phase 1
    # workers (profiler_worker) pass drop_privileges=False so they can
    # still read the root-owned mode-600 temp file the caller created.
    drop_privileges: bool = True


_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


def _prctl_no_new_privs() -> None:
    rc = _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"prctl(PR_SET_NO_NEW_PRIVS) failed: {os.strerror(err)}")


def _make_preexec(limits: SandboxLimits, uid: int, gid: int):
    def _apply():
        # 1. New process group so parent can killpg on timeout.
        os.setsid()

        # 2. Drop privileges only if requested (Phase 2). Phase 1 workers
        #    stay as root so they can read the mode-600 temp file the
        #    caller wrote for them.
        if limits.drop_privileges:
            try:
                os.setgroups([])
            except PermissionError:
                pass
            os.setgid(gid)
            os.setuid(uid)

        # 3. rlimits
        resource.setrlimit(resource.RLIMIT_CPU,    (limits.cpu_seconds,  limits.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS,     (limits.memory_bytes, limits.memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.nofile,       limits.nofile))
        resource.setrlimit(resource.RLIMIT_NPROC,  (limits.nproc,        limits.nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE,  (limits.fsize_bytes,  limits.fsize_bytes))
        resource.setrlimit(resource.RLIMIT_CORE,   (0, 0))

        # 4. Lock privilege state; also prereq for non-root seccomp inside wrapper.
        _prctl_no_new_privs()

    return _apply


_WORKER_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL")


async def run_sandboxed(
    worker_script: str,
    stdin_payload: bytes,
    limits: Optional[SandboxLimits] = None,
) -> SandboxResult:
    lim = limits or SandboxLimits()

    try:
        pw = pwd.getpwnam(SANDBOX_USER)
    except KeyError as e:
        raise SandboxError(
            f"Sandbox user '{SANDBOX_USER}' does not exist. Provision with: "
            f"useradd -u 5000 -M -r -s /usr/sbin/nologin {SANDBOX_USER}"
        ) from e
    uid, gid = pw.pw_uid, pw.pw_gid

    scratch = tempfile.mkdtemp(prefix="evai-sbox-")
    if lim.drop_privileges:
        os.chown(scratch, uid, gid)
    os.chmod(scratch, 0o700)

    r_fd, w_fd = os.pipe()
    os.set_inheritable(w_fd, True)

    env = {k: v for k, v in os.environ.items() if k in _WORKER_ENV_ALLOWLIST}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env["HOME"] = scratch
    env["TMPDIR"] = scratch
    # Result channel: parent tells child which fd number to write JSON to.
    # We deliberately do NOT dup2 in preexec because pass_fds preserves
    # the numeric fd across execve when set_inheritable(fd, True) is set.
    env["EVAI_RESULT_FD"] = str(w_fd)
    # Force single-threaded BLAS/OpenMP so numpy/scipy don't call clone()
    # after seccomp is loaded (clone is in our denylist to block fork-bombs).
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["BLIS_NUM_THREADS"] = "1"

    started = time.monotonic()
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, worker_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=scratch,
            env=env,
            pass_fds=(w_fd,),
            preexec_fn=_make_preexec(lim, uid, gid),
            close_fds=True,
        )
        os.close(w_fd)
        w_fd = -1

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=lim.wall_timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                stdout, stderr = b"", b""

        elapsed = time.monotonic() - started
        exit_code = proc.returncode if proc.returncode is not None else -signal.SIGKILL
        killed_by = (-exit_code) if (exit_code is not None and exit_code < 0) else None

        # Drain result pipe (non-blocking; child closed its write end on exit).
        os.set_blocking(r_fd, False)
        chunks = []
        try:
            while True:
                try:
                    chunk = os.read(r_fd, 65536)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 4 * 1024 * 1024:
                    break
        finally:
            os.close(r_fd)
            r_fd = -1

        result_bytes = b"".join(chunks)
        result_obj: Optional[dict] = None
        if result_bytes:
            try:
                result_obj = json.loads(result_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                result_obj = {"__result_channel_parse_error__": str(e),
                              "raw_preview_hex": result_bytes[:200].hex()}

        return SandboxResult(
            result=result_obj,
            stdout_logs=stdout.decode("utf-8", errors="replace") if stdout else "",
            stderr_logs=stderr.decode("utf-8", errors="replace") if stderr else "",
            elapsed_seconds=elapsed,
            exit_code=exit_code if exit_code is not None else -1,
            timed_out=timed_out,
            killed_by_signal=killed_by,
        )
    finally:
        if r_fd != -1:
            try:
                os.close(r_fd)
            except OSError:
                pass
        if w_fd != -1:
            try:
                os.close(w_fd)
            except OSError:
                pass
        shutil.rmtree(scratch, ignore_errors=True)


# ---- Back-compat shim so Phase 1 profiler_worker.py keeps working -----------
# Phase 1's profiler_worker writes its JSON result to stdout (not fd 3).
# This shim first checks the new fd-3 result channel; if empty, it parses
# stdout as JSON. New Phase 2+ workers must use fd 3 (see EVAI_RESULT_FD).
async def run_worker(script_path: str, stdin_payload: bytes,
                     limits: Optional[SandboxLimits] = None) -> dict:
    # Phase 1 compat: keep root uid so profiler can read its input file.
    lim = limits or SandboxLimits()
    if lim.drop_privileges:
        # Clone with drop_privileges=False to preserve Phase-1 behavior.
        from dataclasses import replace as _replace
        lim = _replace(lim, drop_privileges=False)
    r = await run_sandboxed(script_path, stdin_payload, lim)
    if r.timed_out:
        raise SandboxTimeout(
            f"Sandbox exceeded {(limits or SandboxLimits()).wall_timeout_seconds}s"
        )
    if r.exit_code != 0:
        raise SandboxError(
            f"Sandbox exit={r.exit_code} signal={r.killed_by_signal} "
            f"stderr={r.stderr_logs[:400]}"
        )
    if r.result is not None:
        return r.result
    # Phase-1 fallback: parse stdout as JSON.
    if r.stdout_logs:
        try:
            return json.loads(r.stdout_logs)
        except json.JSONDecodeError as e:
            raise SandboxError(
                f"Sandbox produced non-JSON stdout: {r.stdout_logs[:400]}"
            ) from e
    raise SandboxError("Sandbox produced no result on fd 3 or stdout")


WORKER_DIR = Path(__file__).parent / "workers"
