from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import stat
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import quote

from fastapi import HTTPException, UploadFile

from .config import Settings

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_NOTE_KEYS = {"new", "improved", "fixed", "security", "important"}
MANIFEST_MAX_BYTES = 1024 * 1024


@dataclass
class ReleasePublishResult:
    folder: Path
    version: str
    package_filename: str
    sha256: str


@dataclass(frozen=True)
class PackageManifest:
    version: str
    minimum_version: str
    release_notes: dict[str, list[str]]


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


def _validate_release_notes_value(value: object) -> dict[str, list[str]]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Release notes in manifest.json are empty")
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > 500:
                raise HTTPException(
                    status_code=400,
                    detail="A release notes sentence in manifest.json exceeds 500 characters",
                )
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > 500:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
        return {key: chunks if key == "important" else [] for key in sorted(ALLOWED_NOTE_KEYS)}

    try:
        items_by_category = dict(value) if isinstance(value, dict) else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Release notes must be an object or text") from exc
    if items_by_category is None:
        raise HTTPException(status_code=400, detail="Release notes must be an object or text")

    normalized: dict[str, list[str]] = {key: [] for key in sorted(ALLOWED_NOTE_KEYS)}
    for key, items in items_by_category.items():
        if key not in ALLOWED_NOTE_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported release notes category: {key}",
            )
        if not isinstance(items, list) or not all(
            isinstance(item, str) and item.strip() and len(item.strip()) <= 500 for item in items
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Release notes category '{key}' must contain non-empty strings up to 500 characters",
            )
        normalized[key] = [item.strip() for item in items]

    return normalized


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_package_manifest(package_path: Path, *, expected_product: str) -> PackageManifest:
    try:
        with zipfile.ZipFile(package_path) as archive:
            candidates: list[zipfile.ZipInfo] = []
            for item in archive.infolist():
                if item.is_dir() or "\\" in item.filename:
                    continue
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts:
                    continue
                if path.name == "manifest.json":
                    candidates.append(item)

            if len(candidates) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="ZIP package must contain exactly one manifest.json",
                )

            manifest_info = candidates[0]
            mode = manifest_info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise HTTPException(status_code=400, detail="manifest.json must not be a symbolic link")
            if manifest_info.flag_bits & 0x1:
                raise HTTPException(status_code=400, detail="manifest.json must not be encrypted")
            if manifest_info.file_size > MANIFEST_MAX_BYTES:
                raise HTTPException(status_code=400, detail="manifest.json is too large")

            raw = archive.read(manifest_info)
    except HTTPException:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise HTTPException(status_code=400, detail=f"ZIP package is invalid: {exc}") from exc

    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"manifest.json is invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="manifest.json must contain a JSON object")
    if manifest.get("product") != expected_product:
        raise HTTPException(status_code=400, detail="manifest.json is for a different product")

    version = manifest.get("version")
    minimum_version = manifest.get("minimum_version")
    minimal_version = manifest.get("minimal_version")
    if minimum_version is not None and minimal_version is not None and minimum_version != minimal_version:
        raise HTTPException(
            status_code=400,
            detail="minimum_version and minimal_version in manifest.json do not match",
        )
    if minimum_version is None:
        minimum_version = minimal_version
    if not isinstance(version, str) or not isinstance(minimum_version, str):
        raise HTTPException(
            status_code=400,
            detail="manifest.json must contain version and minimum_version",
        )
    _version_tuple(version)
    _version_tuple(minimum_version)
    if "release_notes" not in manifest:
        raise HTTPException(status_code=400, detail="manifest.json does not contain release_notes")

    return PackageManifest(
        version=version.strip(),
        minimum_version=minimum_version.strip(),
        release_notes=_validate_release_notes_value(manifest["release_notes"]),
    )


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
                "release_notes": metadata.get("release_notes"),
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
    temporary_path = output_root / f".releases-{uuid.uuid4().hex}.tmp"
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(index_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return index_path


def delete_release(settings: Settings, version: str) -> None:
    version = version.strip()
    _version_tuple(version)

    output_root = settings.release_output_root.resolve()
    folder = output_root / version
    if folder.is_symlink():
        raise HTTPException(status_code=400, detail="Release folder must not be a symbolic link")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Release {version} does not exist")

    metadata_path = folder / "release.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise HTTPException(status_code=409, detail=f"Release {version} is incomplete")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=f"Release {version} has invalid metadata") from exc
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise HTTPException(
            status_code=409,
            detail=f"Release metadata does not match folder {version}",
        )

    quarantine_root = output_root.parent / f".{output_root.name}-delete-{uuid.uuid4().hex}"
    quarantine_folder = quarantine_root / version
    try:
        quarantine_root.mkdir()
        folder.rename(quarantine_folder)
    except OSError as exc:
        shutil.rmtree(quarantine_root, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Release {version} could not be removed") from exc

    try:
        write_release_index(settings)
    except Exception as exc:
        try:
            quarantine_folder.rename(folder)
            quarantine_root.rmdir()
        except OSError as rollback_exc:
            raise HTTPException(
                status_code=500,
                detail=f"Release index update and rollback failed for {version}",
            ) from rollback_exc
        raise HTTPException(
            status_code=500,
            detail=f"Release index could not be updated; release {version} was restored",
        ) from exc

    shutil.rmtree(quarantine_root, ignore_errors=True)


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


async def inspect_release_package(
    settings: Settings,
    package_upload: UploadFile,
) -> PackageManifest:
    filename = Path(package_upload.filename or "").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Package must be a .zip file")

    with TemporaryDirectory(prefix="hads-release-inspect-") as temp_dir:
        temp_package = Path(temp_dir) / filename
        await _save_upload(package_upload, temp_package)
        return read_package_manifest(
            temp_package,
            expected_product=settings.release_product,
        )


async def publish_release(
    settings: Settings,
    *,
    release_date: str,
    mandatory: bool,
    title: str,
    summary: str,
    package_upload: UploadFile,
) -> ReleasePublishResult:
    filename = Path(package_upload.filename or "").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Package must be a .zip file")

    output_root = settings.release_output_root
    output_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="hads-release-upload-") as temp_dir:
        temp_package = Path(temp_dir) / filename
        await _save_upload(package_upload, temp_package)
        package_manifest = read_package_manifest(
            temp_package,
            expected_product=settings.release_product,
        )
        version = package_manifest.version
        minimum_version = package_manifest.minimum_version
        notes = package_manifest.release_notes

        folder = output_root / version
        if folder.exists():
            raise HTTPException(status_code=409, detail=f"Release {version} already exists")

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
