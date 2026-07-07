"""Malware scan (ClamAV) + Excel/CSV structural hardening.

Threat model: malicious upload of CSV/Excel. Real risks:
- Excel with VBA macros / DDE / OLE objects / external links.
- CSV formula injection (cells beginning with =/+/-/@) when opened in Excel.
- Generic virus signatures embedded in files.
"""
from __future__ import annotations
import asyncio
import io
import logging
import zipfile
from dataclasses import dataclass

import clamd

from .config import get_settings

logger = logging.getLogger(__name__)


# ---------- ClamAV ----------
class ScanError(Exception):
    pass


class ScanUnavailable(Exception):
    pass


class VirusDetected(Exception):
    def __init__(self, signature: str) -> None:
        super().__init__(signature)
        self.signature = signature


def _client() -> clamd.ClamdUnixSocket:
    return clamd.ClamdUnixSocket(path=get_settings().clamd_socket)


async def scan_bytes(data: bytes) -> None:
    """Scan a small blob. Raises ScanUnavailable / VirusDetected on failure."""
    settings = get_settings()

    def _run():
        try:
            cd = _client()
            cd.ping()
            return cd.instream(io.BytesIO(data))
        except Exception as e:  # noqa: BLE001
            raise ScanUnavailable(str(e)) from e

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=settings.clamav_scan_timeout)
    except asyncio.TimeoutError as e:
        raise ScanUnavailable(f"clamd scan timeout after {settings.clamav_scan_timeout}s") from e

    if not result:
        raise ScanUnavailable("clamd returned no result")
    status, signature = result.get("stream", ("ERROR", "unknown"))
    if status == "FOUND":
        raise VirusDetected(signature)
    if status != "OK":
        raise ScanUnavailable(f"clamd status={status} sig={signature}")


async def scanner_healthy() -> tuple[bool, str]:
    def _ping():
        try:
            return _client().ping() == "PONG"
        except Exception as e:  # noqa: BLE001
            return f"error:{e}"
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_ping), timeout=5)
        if result is True:
            return True, "PONG"
        return False, str(result)
    except asyncio.TimeoutError:
        return False, "timeout"


# ---------- Excel / CSV structural hardening ----------
# xlsx zip subpath fragments that indicate dangerous content.
_XLSX_BLOCKED_MEMBERS = (
    "xl/vbaProject.bin",     # macros
    "xl/embeddings/",        # OLE embedded objects
    "xl/externalLinks/",     # DDE / external refs
)

# CSV cells starting with any of these are treated as formula-injection attempts.
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class HardenResult:
    ok: bool
    reason: str | None = None


def harden_xlsx(data: bytes) -> HardenResult:
    """Reject xlsx if it contains macros, embedded objects, or external links."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                for blocked in _XLSX_BLOCKED_MEMBERS:
                    if name == blocked or name.startswith(blocked):
                        return HardenResult(
                            ok=False,
                            reason=f"blocked_content:{name}",
                        )
    except zipfile.BadZipFile:
        return HardenResult(ok=False, reason="invalid_xlsx")
    return HardenResult(ok=True)


def harden_csv(data: bytes, max_scan_bytes: int = 1_000_000) -> HardenResult:
    """Reject CSVs whose first cell of any row triggers formula injection.

    Only inspects the first ``max_scan_bytes`` for performance; anything
    beyond that is trusted-by-omission but the file itself is still stored
    encrypted and only accessed by our own profiler in a sandbox.
    """
    sample = data[:max_scan_bytes]
    try:
        text_str = sample.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            text_str = sample.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return HardenResult(ok=False, reason="invalid_encoding_not_utf8")

    for line_no, line in enumerate(text_str.splitlines(), start=1):
        if not line:
            continue
        # Check every cell in the line, not just the first.
        for cell in line.split(","):
            stripped = cell.lstrip('"').lstrip()
            if not stripped:
                continue
            if stripped[0] in _CSV_FORMULA_TRIGGERS:
                return HardenResult(
                    ok=False,
                    reason=f"csv_formula_injection:line={line_no}",
                )
    return HardenResult(ok=True)


ALLOWED_EXT_TO_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def detect_and_harden(filename: str, data: bytes) -> HardenResult:
    """Enforce allowlist and hardening. Rejects .xls, .xlsm, .xlsb outright."""
    lower = filename.lower()
    if lower.endswith(".xlsm") or lower.endswith(".xlsb") or lower.endswith(".xls"):
        return HardenResult(ok=False, reason="disallowed_extension:macro_capable_or_legacy")
    if lower.endswith(".xlsx"):
        return harden_xlsx(data)
    if lower.endswith(".csv"):
        return harden_csv(data)
    return HardenResult(ok=False, reason="disallowed_extension")
