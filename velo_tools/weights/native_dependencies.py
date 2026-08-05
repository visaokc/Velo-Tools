from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import urllib.request
import zipfile


LOCK_PATH = Path(__file__).with_name("_native_dependencies.json")
DOWNLOAD_TIMEOUT = 30


def _platform_key() -> str:
    return f"Python{sys.version_info.major}{sys.version_info.minor}-win_amd64"


def _load_dependencies() -> list[dict]:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise RuntimeError("不支持的 Robust 依赖清单版本")
    dependencies = payload.get("platforms", {}).get(_platform_key())
    if not dependencies:
        raise RuntimeError(f"当前 Python 平台不受支持: {_platform_key()}")
    return list(dependencies)


def download_size_bytes() -> int:
    return sum(int(item.get("size", 0)) for item in _load_dependencies())


def installed_size_bytes() -> int:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return int(payload.get("installed_size", 0))


def _lock_fingerprint() -> str:
    return hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()[:16]


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return (
        Path(base)
        / "VeloTools"
        / "native-deps"
        / _platform_key()
        / _lock_fingerprint()
    )


def site_packages_path() -> Path:
    return cache_root() / "site-packages"


def is_installed() -> bool:
    root = site_packages_path()
    marker = root / ".velo-native-deps.json"
    return marker.is_file() and (
        (root / "scipy" / "__init__.py").is_file()
        and (root / "igl").exists()
        and (root / "robust_laplacian.py").is_file()
    )


def _download(item: dict, destination: Path) -> None:
    request = urllib.request.Request(
        str(item["url"]),
        headers={"User-Agent": f"Python/{sys.version_info.major}.{sys.version_info.minor}"},
    )
    digest = hashlib.sha256()
    context = ssl.create_default_context()
    with urllib.request.urlopen(
        request, context=context, timeout=DOWNLOAD_TIMEOUT
    ) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
    expected = str(item["sha256"]).casefold()
    if digest.hexdigest().casefold() != expected:
        raise RuntimeError(f"{item['filename']} SHA256 校验失败")


def install() -> Path:
    dependencies = _load_dependencies()
    root = cache_root()
    downloads = root / "downloads"
    staging = root / "site-packages.staging"
    downloads.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for item in dependencies:
            wheel = downloads / str(item["filename"])
            expected = str(item["sha256"]).casefold()
            valid = wheel.is_file() and hashlib.sha256(wheel.read_bytes()).hexdigest().casefold() == expected
            if not valid:
                wheel.unlink(missing_ok=True)
                partial = wheel.with_suffix(wheel.suffix + ".download")
                partial.unlink(missing_ok=True)
                try:
                    _download(item, partial)
                    partial.replace(wheel)
                finally:
                    partial.unlink(missing_ok=True)
            with zipfile.ZipFile(wheel) as archive:
                for entry in archive.infolist():
                    path = Path(entry.filename)
                    if path.is_absolute() or ".." in path.parts:
                        raise RuntimeError(f"wheel 包含不安全路径: {entry.filename}")
                archive.extractall(staging)
        (staging / ".velo-native-deps.json").write_text(
            json.dumps({"fingerprint": _lock_fingerprint()}),
            encoding="utf-8",
        )
        destination = site_packages_path()
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
        shutil.rmtree(downloads, ignore_errors=True)
        return destination
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
