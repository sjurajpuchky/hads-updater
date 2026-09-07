from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi import HTTPException, UploadFile

from app.config import Settings
from app.release_service import publish_release, read_package_manifest


def write_package(path: Path, manifest: dict, *, manifest_name: str = "HADS_Update_9.8.7/manifest.json") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(manifest_name, json.dumps(manifest, ensure_ascii=False))
        archive.writestr("HADS_Update_9.8.7/payload/example.txt", "payload")


def valid_manifest(**overrides: object) -> dict:
    value = {
        "product": "HADS",
        "version": "9.8.7",
        "minimum_version": "1.34.4",
        "release_notes": "Opraveno načítání aktualizací. Přidána kontrola balíčku.",
    }
    value.update(overrides)
    return value


class PackageManifestTests(unittest.TestCase):
    def test_reads_nested_manifest_and_normalizes_text_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.zip"
            write_package(package, valid_manifest())

            result = read_package_manifest(package, expected_product="HADS")

        self.assertEqual(result.version, "9.8.7")
        self.assertEqual(result.minimum_version, "1.34.4")
        self.assertEqual(set(result.release_notes), {"new", "improved", "fixed", "security", "important"})
        self.assertEqual(len(result.release_notes["important"]), 1)

    def test_accepts_minimal_version_alias(self):
        manifest = valid_manifest(minimal_version="1.40.0")
        del manifest["minimum_version"]
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.zip"
            write_package(package, manifest)
            result = read_package_manifest(package, expected_product="HADS")
        self.assertEqual(result.minimum_version, "1.40.0")

    def test_preserves_structured_release_notes(self):
        notes = {
            "new": ["Nová funkce"],
            "improved": [],
            "fixed": ["Oprava"],
            "security": [],
            "important": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.zip"
            write_package(package, valid_manifest(release_notes=notes))
            result = read_package_manifest(package, expected_product="HADS")
        self.assertEqual(result.release_notes, notes)

    def test_rejects_more_than_one_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                raw = json.dumps(valid_manifest())
                archive.writestr("first/manifest.json", raw)
                archive.writestr("second/manifest.json", raw)
            with self.assertRaises(HTTPException) as caught:
                read_package_manifest(package, expected_product="HADS")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("exactly one", caught.exception.detail)

    def test_rejects_wrong_product(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "update.zip"
            write_package(package, valid_manifest(product="OTHER"))
            with self.assertRaises(HTTPException) as caught:
                read_package_manifest(package, expected_product="HADS")
        self.assertIn("different product", caught.exception.detail)


class PublishManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_published_metadata_uses_values_from_manifest(self):
        notes = {
            "new": ["Načteno z manifestu"],
            "improved": [],
            "fixed": [],
            "security": [],
            "important": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "source.zip"
            write_package(
                package,
                valid_manifest(
                    version="9.9.0",
                    minimum_version="1.45.0",
                    release_notes=notes,
                ),
            )
            settings = Settings(
                app_name="Test",
                session_secret="test",
                admin_username="test",
                admin_password="test",
                release_product="HADS",
                release_output_root=root / "releases",
                release_private_key=root / "unused.pem",
            )
            upload = UploadFile(filename="HADS_Update_9.9.0.zip", file=io.BytesIO(package.read_bytes()))

            with mock.patch("app.release_service._sign", return_value=b"signature"):
                result = await publish_release(
                    settings,
                    release_date="2026-09-07",
                    mandatory=False,
                    title="HADS 9.9.0",
                    summary="Testovací vydání",
                    package_upload=upload,
                )

            metadata = json.loads((result.folder / "release.json").read_text(encoding="utf-8"))
            index = json.loads((settings.release_output_root / "releases.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["version"], "9.9.0")
        self.assertEqual(metadata["minimum_version"], "1.45.0")
        self.assertEqual(metadata["release_notes"], notes)
        self.assertEqual(index["current"]["version"], "9.9.0")
        self.assertEqual(index["current"]["release_notes"], notes)


if __name__ == "__main__":
    unittest.main()
