"""Phase-2 sandbox proof harness.

Runs 4 tests through the actual sandbox (app.sandbox.run_sandboxed +
app.workers.stats_worker) and prints their real outputs:

  1. Network attack : socket.socket().connect(("1.1.1.1", 80))
  2. Filesystem attack : open("/etc/passwd") + open("/app/backend/.env")
  3. Timeout attack : while True: pass
  4. Legitimate correlation + IQR outlier detection on synthetic data
"""
from __future__ import annotations
import asyncio
import io
import json
import struct
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.sandbox import SandboxLimits, run_sandboxed  # noqa: E402

WORKER = str(BACKEND / "app" / "workers" / "stats_worker.py")


def payload(code: str, csv_bytes: bytes = b"", input_format: str = "csv") -> bytes:
    ctrl = json.dumps({"code": code, "input_format": input_format}).encode("utf-8")
    return struct.pack(">I", len(ctrl)) + ctrl + csv_bytes


def _print_header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _print_result(res) -> None:
    print(f"  exit_code         = {res.exit_code}")
    print(f"  killed_by_signal  = {res.killed_by_signal}"
          f"{' (SIGSYS = seccomp)' if res.killed_by_signal == 31 else ''}")
    print(f"  timed_out         = {res.timed_out}")
    print(f"  elapsed_seconds   = {res.elapsed_seconds:.3f}")
    print(f"  stdout_logs       = {res.stdout_logs!r}")
    stderr_preview = res.stderr_logs.strip().splitlines()[-4:] if res.stderr_logs else []
    print(f"  stderr_logs(tail) = {stderr_preview}")
    print(f"  result (fd 3)     = {json.dumps(res.result, default=str)[:600] if res.result else None}")


async def test1_network(limits: SandboxLimits) -> None:
    _print_header("TEST 1  Network attack: socket().connect() must be killed by seccomp")
    code = (
        "import socket\n"                # this will raise NameError first (no __import__)
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.connect(('1.1.1.1', 80))\n"
        "RESULT = {'unexpected': 'network was reachable'}\n"
    )
    # NOTE: the exec globals lack __import__, so `import socket` in user code
    # raises NameError. To *actually* test the kernel-level block, we bypass
    # the restricted-globals layer and invoke the socket syscall directly via
    # ctypes, which the LLM could theoretically emit if it were smart. This
    # is the harder test.
    code = (
        "import ctypes  # will fail: no __import__ in safe builtins\n"
        "RESULT = {'this_line_never_runs': True}\n"
    )
    # Even harder: reach the syscall through pre-imported numpy/pandas C ext.
    # We use pd.read_csv() with a URL, which internally uses urllib3 -> socket.
    code = (
        "# Adversary attempts network via pandas -> urllib -> socket.\n"
        "# pandas at read_csv URL time calls urllib.request.urlopen which\n"
        "# eventually calls socket.socket(). Since 'urllib' isn't in globals\n"
        "# and __import__ is banned, this line already fails at NameError.\n"
        "# So we simulate the deeper adversary: call the raw socket syscall\n"
        "# via the *already-loaded* libc via the numpy ctypes ABI hack.\n"
        "# In practice we just try to use pandas over an http URL:\n"
        "df2 = pd.read_csv('http://1.1.1.1/x.csv')\n"
        "RESULT = {'net_ok': True, 'rows': len(df2)}\n"
    )
    res = await run_sandboxed(WORKER, payload(code, b"", "none"), limits)
    _print_result(res)


async def test1b_network_ctypes(limits: SandboxLimits) -> None:
    _print_header("TEST 1b  Network attack via raw libc socket() syscall through numpy's ctypes bridge")
    # Adversary path: numpy is pre-imported and pulls in ctypes internally
    # for some codepaths. Even without __import__, an attacker could look up
    # ctypes on np: `np.ctypeslib.ctypes`. Use that to reach libc.socket()
    # directly. Seccomp should still block the socket syscall.
    code = (
        "ct = np.ctypeslib.ctypes\n"
        "libc = ct.CDLL('libc.so.6', use_errno=True)\n"
        "AF_INET = 2\n"
        "SOCK_STREAM = 1\n"
        "fd = libc.socket(AF_INET, SOCK_STREAM, 0)  # <-- seccomp should KILL here\n"
        "RESULT = {'unexpected_socket_fd': fd}\n"
    )
    res = await run_sandboxed(WORKER, payload(code, b"", "none"), limits)
    _print_result(res)


async def test2_filesystem(limits: SandboxLimits) -> None:
    _print_header("TEST 2  Filesystem attack: open('/etc/passwd') and open('/app/backend/.env')")
    # NB: open() isn't in safe builtins either, but adversary can try to reach
    # the openat() syscall through pandas (pd.read_csv on a path) which is
    # the classic escape. That must hit seccomp.
    code = (
        "leak = {}\n"
        "try:\n"
        "    df2 = pd.read_csv('/etc/passwd', header=None)\n"
        "    leak['etc_passwd_rows'] = len(df2)\n"
        "except Exception as e:\n"
        "    leak['etc_passwd_error'] = type(e).__name__ + ':' + str(e)[:120]\n"
        "try:\n"
        "    df3 = pd.read_csv('/app/backend/.env', header=None)\n"
        "    leak['env_rows'] = len(df3)\n"
        "except Exception as e:\n"
        "    leak['env_error'] = type(e).__name__ + ':' + str(e)[:120]\n"
        "RESULT = leak\n"
    )
    res = await run_sandboxed(WORKER, payload(code, b"", "none"), limits)
    _print_result(res)


async def test3_timeout(limits: SandboxLimits) -> None:
    _print_header(f"TEST 3  Timeout attack: infinite loop must be killed at t={limits.wall_timeout_seconds}s")
    code = "while True:\n    pass\n"
    t0 = time.monotonic()
    res = await run_sandboxed(WORKER, payload(code, b"", "none"), limits)
    wall = time.monotonic() - t0
    print(f"  outer wall elapsed = {wall:.3f}s")
    _print_result(res)


async def test4_legitimate(limits: SandboxLimits) -> None:
    _print_header("TEST 4  Legitimate: correlation + IQR outlier detection on real synthetic data")
    # Two strongly positively correlated columns + one column with outliers.
    import pandas as pd
    import numpy as np
    rng = np.random.RandomState(42)
    n = 500
    x = rng.normal(50, 10, n)
    y = 2.0 * x + rng.normal(0, 3, n)         # tightly correlated with x
    z = rng.normal(0, 1, n)                   # independent of x
    # Insert 5 obvious outliers into z
    z[[10, 20, 30, 40, 50]] = [200.0, -175.0, 220.0, -190.0, 205.0]
    df = pd.DataFrame({"x": x, "y": y, "z": z})
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    from app.agents.statistical_agent import _classify, generate_code

    # Profile that Phase-1 profiler would have produced (just enough shape):
    profile_cols = [
        {"name": "x", "type": "numeric", "null_count": 0, "cardinality": n},
        {"name": "y", "type": "numeric", "null_count": 0, "cardinality": n},
        {"name": "z", "type": "numeric", "null_count": 0, "cardinality": n},
    ]
    plan = _classify(profile_cols)
    print(f"  tests_selected = {plan.tests}")
    code = generate_code(plan)
    # Also inject a print() to prove stdout pollution is contained.
    code = "print('hello from generated code - this MUST NOT corrupt RESULT')\n" + code
    res = await run_sandboxed(WORKER, payload(code, csv_bytes, "csv"), limits)
    _print_result(res)
    # Sanity assertions on the numbers.
    if res.result and res.result.get("ok"):
        r = res.result["result"]
        pearson = r.get("pearson_correlation", {}).get("matrix")
        outliers = r.get("iqr_outliers", {})
        print(f"  Pearson matrix rows:")
        cols = r.get("pearson_correlation", {}).get("columns")
        for name, row in zip(cols, pearson):
            print(f"    {name}: " + "  ".join(f"{v:+.4f}" for v in row))
        z_out = outliers.get("z", {})
        print(f"  IQR outliers on z: count={z_out.get('outlier_count')}, "
              f"indices={z_out.get('outlier_indices')}")
        print(f"  IQR outliers on x: count={outliers.get('x', {}).get('outlier_count')}")


async def main() -> None:
    limits = SandboxLimits(cpu_seconds=30, memory_bytes=1024 * 1024 * 1024,
                           wall_timeout_seconds=5)
    # Use 5s wall for the timeout test so we don't wait 20s; the mechanism
    # is identical either way. All non-timeout tests use the same 5s limit
    # -- more than enough headroom for correlation on 500 rows.
    await test1_network(limits)
    await test1b_network_ctypes(limits)
    await test2_filesystem(limits)
    await test3_timeout(limits)
    await test4_legitimate(limits)


if __name__ == "__main__":
    asyncio.run(main())
