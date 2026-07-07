"""Statistical Analysis Agent — sandbox-side wrapper.

Runs as uid=5000 (evsandbox) inside app/sandbox.py's isolated child.

Protocol on stdin (fd 0):
    First 4 bytes  : big-endian uint32 = length of control JSON
    Next N bytes   : control JSON = {"code": "<python source>",
                                     "input_format": "csv"|"none"}
    Rest of stream : raw dataset bytes (if input_format == "csv")

Protocol on fd 3 (result channel):
    Single JSON document written by this wrapper. Callers parse fd 3
    ONLY; stdout is captured separately as freeform logs and is never
    parsed for results. This is how print() calls in generated code
    cannot pollute or spoof the result payload.

Seccomp:
    A libseccomp BPF filter is loaded AFTER all Python imports and
    numeric-library warmups. Default action is SCMP_ACT_ALLOW; the
    following syscalls are explicitly denied with SCMP_ACT_KILL_PROCESS
    (kernel kills the process with SIGSYS on the offending syscall):
        network:  socket, socketpair, connect, bind, listen, accept,
                  accept4, sendto, sendmsg, sendmmsg, recvfrom, recvmsg,
                  setsockopt, getsockopt, shutdown
        fs open:  open, openat, openat2, creat, name_to_handle_at,
                  open_by_handle_at
        exec/esc: execve, execveat, ptrace, process_vm_readv,
                  process_vm_writev, chroot, pivot_root, mount, umount2,
                  unshare, setns, personality, kexec_load, bpf,
                  clone, clone3, fork, vfork
        keys:     keyctl, add_key, request_key
        io_uring: io_uring_setup, io_uring_enter, io_uring_register

This is a denylist rather than an allowlist because the aarch64 CPython
runtime issues a wide and evolving set of internal syscalls (rseq,
madvise, etc.) and the goal here is to positively block the categories
you asked me to block (network, fs-open, exec, escape). If new
categories need blocking, add them to _DENIED_SYSCALLS below.
"""
from __future__ import annotations
import io
import json
import os
import struct
import sys
import traceback


# Parent tells us which fd number to write result JSON to via env.
_RESULT_FD = int(os.environ.get("EVAI_RESULT_FD", "3"))


def _read_exact(fd: int, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _read_all(fd: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _emit(result_obj) -> None:
    """Write the single result JSON to the result-channel fd and close it."""
    try:
        data = json.dumps(result_obj, default=str).encode("utf-8")
    except Exception as e:  # noqa: BLE001
        data = json.dumps(
            {"ok": False, "error": "result_not_json_serializable", "detail": str(e)}
        ).encode("utf-8")
    try:
        os.write(_RESULT_FD, data)
    finally:
        try:
            os.close(_RESULT_FD)
        except OSError:
            pass


_DENIED_SYSCALLS = [
    # network
    "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
    "sendto", "sendmsg", "sendmmsg", "recvfrom", "recvmsg",
    "setsockopt", "getsockopt", "shutdown",
    # any pathname-based open (also prevents late dlopen of new .so files)
    "open", "openat", "openat2", "creat",
    "name_to_handle_at", "open_by_handle_at",
    # exec / process manipulation / escape
    "execve", "execveat", "ptrace",
    "process_vm_readv", "process_vm_writev",
    "chroot", "pivot_root", "mount", "umount2", "unshare", "setns",
    "personality", "kexec_load", "bpf",
    "clone", "clone3", "fork", "vfork",
    # kernel keyring
    "keyctl", "add_key", "request_key",
    # io_uring: separate async syscall path that could bypass seccomp on reads
    "io_uring_setup", "io_uring_enter", "io_uring_register",
]


def _install_seccomp_filter() -> list:
    """Install the seccomp filter. Returns the list of syscall names that
    were actually installed (some are arch-specific)."""
    import pyseccomp as sc  # imported lazily to avoid affecting Phase 1 workers
    f = sc.SyscallFilter(sc.ALLOW)  # default = allow
    installed = []
    for name in _DENIED_SYSCALLS:
        try:
            f.add_rule(sc.KILL_PROCESS, name)
            installed.append(name)
        except Exception:  # noqa: BLE001
            # Syscall not present on this architecture -> nothing to block.
            pass
    f.load()
    return installed


_SAFE_BUILTINS = {
    name: getattr(__builtins__, name) if hasattr(__builtins__, name)
          else __builtins__[name]  # type: ignore
    for name in (
        "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "hex", "int", "isinstance", "issubclass", "iter",
        "len", "list", "map", "max", "min", "next", "object", "oct",
        "ord", "pow", "print", "range", "repr", "reversed", "round",
        "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
        "True", "False", "None",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "ArithmeticError", "ZeroDivisionError", "OverflowError",
        "AttributeError", "RuntimeError", "StopIteration",
    )
}


def _run_user_code(code: str, df, pd, np, scipy, stats, math_mod):
    globs = {
        "__builtins__": _SAFE_BUILTINS,
        "__name__": "__stats_agent__",
        "df": df,
        "pd": pd,
        "np": np,
        "scipy": scipy,
        "stats": stats,
        "math": math_mod,
        "RESULT": None,
    }
    exec(compile(code, "<agent_code>", "exec"), globs, globs)  # noqa: S102
    return globs.get("RESULT")


def main() -> int:
    # ---- 1. Read control message + dataset from stdin (BEFORE seccomp) ----
    header = _read_exact(0, 4)
    if len(header) != 4:
        _emit({"ok": False, "error": "no_control_header"})
        return 2
    (ctrl_len,) = struct.unpack(">I", header)
    if ctrl_len <= 0 or ctrl_len > 4 * 1024 * 1024:
        _emit({"ok": False, "error": "control_length_invalid", "len": ctrl_len})
        return 2
    ctrl_bytes = _read_exact(0, ctrl_len)
    try:
        control = json.loads(ctrl_bytes.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": "control_not_json", "detail": str(e)})
        return 2

    code = control.get("code", "")
    input_format = control.get("input_format", "none")
    dataset_bytes = _read_all(0) if input_format == "csv" else b""

    # ---- 2. Eager import numeric stack + warm lazy paths ----
    import math as math_mod  # noqa: E402
    import pandas as pd  # noqa: E402
    import numpy as np  # noqa: E402
    import scipy  # noqa: E402
    from scipy import stats as _stats  # noqa: E402

    # Warm lazy dlopens so seccomp doesn't kill us later on first use.
    _warm_df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]})
    _ = _warm_df.corr(method="pearson")
    _ = _warm_df.corr(method="spearman")
    _ = _warm_df.describe()
    _ = _warm_df["a"].quantile([0.25, 0.5, 0.75])
    _ = _stats.pearsonr(_warm_df["a"].values, _warm_df["b"].values)
    _ = _stats.spearmanr(_warm_df["a"].values, _warm_df["b"].values)
    _ = _stats.zscore(_warm_df["a"].values)
    _ = np.random.RandomState(0).randn(4)

    # ---- 3. Materialize the actual dataset (still pre-seccomp) ----
    df = None
    if input_format == "csv":
        try:
            df = pd.read_csv(io.BytesIO(dataset_bytes))
        except Exception as e:  # noqa: BLE001
            _emit({"ok": False, "error": "csv_parse_failed", "detail": str(e)})
            return 2

    # ---- 4. Install seccomp filter (point of no return) ----
    try:
        installed = _install_seccomp_filter()
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": "seccomp_install_failed", "detail": str(e)})
        return 3

    # ---- 5. Execute the (untrusted) LLM/agent-generated code ----
    try:
        result_value = _run_user_code(code, df, pd, np, scipy, _stats, math_mod)
        _emit({
            "ok": True,
            "result": result_value,
            "seccomp_denied_syscalls": installed,
        })
        return 0
    except Exception as e:  # noqa: BLE001
        # Attempt best-effort structured error. Traceback may need syscalls we
        # allow (write to fd 2). All fs-open syscalls are denied, but the
        # traceback module reads source lines from an in-memory string
        # ("<agent_code>") so no file I/O required.
        tb = traceback.format_exc(limit=6)
        _emit({
            "ok": False,
            "error": type(e).__name__,
            "message": str(e),
            "traceback": tb,
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
