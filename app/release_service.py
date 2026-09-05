from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from fastapi import HTTPException, UploadFile

from .config import Settings

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_NOTE_KEYS = {"new", "improved", "fixed", "security", "important"}


@dataclass
class ReleasePublishResult:
    folder: Path
    version: str
    package_filename: str
    sha256: str


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(value.strip()):
        raise HTTPException(
            status_code=400, detail="Version and minimum version must be in X.Y.Z format"
        )
    return tuple(int(part) for part in value.split("."))


def _release_links(
    version: str,
    package_filename: str,
    *,
    base_url: str = "",
) -> dict[str, str]:
    safe_base = base_url.rstrip("/")
    version_part = quote(version, safe="")
    package_part = quote(package_filename, safe="")
    root = f"{safe_base}/release-files/{version_part}"
    return {
        "release_url": f"{root}/release.json",
        "signature_url": f"{root}/release.json.sig",
        "package_url": f"{root}/{package_part}",
    }


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_release_notes(notes_text: str) -> dict[str, list[str]]:
    try:
        value = json.loads(notes_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Release notes JSON is invalid: {exc}") from exc

    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Release notes must be a JSON object")

    normalized: dict[str, list[str]] = {}
    for key, items in value.items():
        if key not in ALLOWED_NOTE_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported release notes category: {key}",
            )
        if not isinstance(items, list) or not all(isinstance(item, str) and item.strip() for item in items):
            raise HTTPException(
                status_code=400,
                detail=f"Release notes category '{key}' must be a list of non-empty strings",
            )
        normalized[key] = [item.strip() for item in items]

    return normalized


def collect_release_index(settings: Settings, *, base_url: str = "") -> dict:
    releases: list[dict] = []
    output_root = settings.release_output_root

    if output_root.exists():
        for folder in sorted(output_root.iterdir()):
            if not folder.is_dir():
                continue

            release_file = folder / "release.json"
            if not release_file.is_file():
                continue

            try:
                metadata = json.loads(release_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue

            version = metadata.get("version")
            package_filename = metadata.get("package_filename")
            if not isinstance(version, str) or not isinstance(package_filename, str):
                continue

            item = {
                "version": version,
                "release_date": metadata.get("release_date"),
                "minimum_version": metadata.get("minimum_version"),
                "mandatory": metadata.get("mandatory"),
                "title": metadata.get("title"),
                "summary": metadata.get("summary"),
                "sha256": metadata.get("sha256"),
                "byte_size": metadata.get("byte_size"),
                **_release_links(version, package_filename, base_url=base_url),
            }
            releases.append(item)

    releases.sort(key=lambda item: _version_tuple(item["version"]), reverse=True)
    current_version = releases[0]["version"] if releases else None
    for item in releases:
        item["current"] = item["version"] == current_version

    current = next((item for item in releases if item["current"]), None)
    return {
        "product": settings.release_product,
        "current": current,
        "releases": releases,
    }


def write_release_index(settings: Settings) -> Path:
    output_root = settings.release_output_root
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "releases.json"
    payload = collect_release_index(settings)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index_path


def _sign(data: bytes, private_key: Path) -> bytes:
    if not private_key.is_file() or private_key.is_symlink():
        raise HTTPException(status_code=500, detail="Configured private key path is not a safe file")

    try:
        completed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(private_key)],
            input=data,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"OpenSSL is not available: {exc}") from exc

    if completed.returncode != 0 or not completed.stdout:
        message = completed.stderr.decode("utf-8", "replace").strip() or "unknown signing error"
        raise HTTPException(status_code=500, detail=f"Signing failed: {message}")

    return completed.stdout


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as target:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
    await upload.close()


async def publish_release(
    settings: Settings,
    *,
    version: str,
    release_date: str,
    minimum_version: str,
    mandatory: bool,
    title: str,
    summary: str,
    notes_json: str,
    package_upload: UploadFile,
) -> ReleasePublishResult:
    _version_tuple(version)
    _version_tuple(minimum_version)
    notes = _validate_release_notes(notes_json)

    filename = Path(package_upload.filename or "").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Package must be a .zip file")

    output_root = settings.release_output_root
    output_root.mkdir(parents=True, exist_ok=True)
    folder = output_root / version
    if folder.exists():
        raise HTTPException(status_code=409, detail=f"Release {version} already exists")

    with TemporaryDirectory(prefix="hads-release-upload-") as temp_dir:
        temp_package = Path(temp_dir) / filename
        await _save_upload(package_upload, temp_package)

        folder.mkdir(parents=True)
        try:
            target_package = folder / filename
            shutil.copy2(temp_package, target_package)

            metadata = {
                "product": settings.release_product,
                "version": version,
                "release_date": release_date,
                "package_filename": target_package.name,
                "byte_size": target_package.stat().st_size,
                "sha256": _sha256(target_package),
                "minimum_version": minimum_version,
                "mandatory": mandatory,
                "title": title.strip(),
                "summary": summary.strip(),
                "release_notes": notes,
            }
            raw = _canonical_json(metadata)
            signature = _sign(raw, settings.release_private_key)

            (folder / "release.json").write_bytes(raw + b"\n")
            (folder / "release.json.sig").write_text(
                base64.b64encode(signature).decode("ascii") + "\n",
                encoding="ascii",
            )
            write_release_index(settings)

            return ReleasePublishResult(
                folder=folder,
                version=version,
                package_filename=target_package.name,
                sha256=metadata["sha256"],
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
