"""Runtime-parity r16 DDS fingerprint generation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .fingerprint import FingerprintError


_HELPER = Path(__file__).with_name("native") / "r16_fingerprint.exe"
_PREFIX = "v3:r16:rgba8-phash:"


def fingerprint_dds_r16(path: str | Path) -> str:
    """Return the exact r16 payload produced by the D3D11 runtime sampling path."""
    if not _HELPER.is_file():
        raise FingerprintError(f"Missing runtime fingerprint helper: {_HELPER}")

    source = Path(path)
    try:
        result = subprocess.run(
            [str(_HELPER), str(source)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise FingerprintError(
            f"Cannot run runtime fingerprint helper for {source}: {exc}"
        ) from exc

    fingerprint = result.stdout.strip()
    if result.returncode != 0 or not fingerprint.startswith(_PREFIX):
        detail = result.stderr.strip() or fingerprint or f"exit {result.returncode}"
        raise FingerprintError(
            f"Runtime fingerprint generation failed for {source}: {detail}"
        )
    return fingerprint
