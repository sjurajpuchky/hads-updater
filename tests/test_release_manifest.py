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
from app.release_service import (
    delete_release,
    publish_release,
    read_package_manifest,
    write_release_index,
)


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


class DeleteReleaseTests(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        return Settings(
            app_name="Test",
            session_secret="test",
            admin_username="test",
            admin_password="test",
            release_product="HADS",
            release_output_root=root / "releases",
            release_private_key=root / "unused.pem",
        )

    def add_release(self, settings: Settings, version: str) -> Path:
        folder = settings.release_output_root / version
        folder.mkdir(parents=True)
        package = folder / f"HADS_Update_{version}.zip"
        package.write_bytes(b"package")
        (folder / "release.json").write_text(
            json.dumps(
                {
                    "product": "HADS",
                    "version": version,
                    "release_date": "2026-09-11",
                    "package_filename": package.name,
                    "byte_size": package.stat().st_size,
                    "sha256": "test",
                    "minimum_version": "1.0.0",
                    "mandatory": False,
                    "title": f"HADS {version}",
                    "summary": "Test",
                    "release_notes": {},
                }
            ),
            encoding="utf-8",
        )
        return folder

    def test_deletes_release_and_promotes_next_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_settings(Path(temp_dir))
            old_folder = self.add_release(settings, "1.2.0")
            current_folder = self.add_release(settings, "1.3.0")
            write_release_index(settings)

            delete_release(settings, "1.3.0")

            index = json.loads(
                (settings.release_output_root / "releases.json").read_text(encoding="utf-8")
            )
            self.assertFalse(current_folder.exists())
            self.assertTrue(old_folder.is_dir())
            self.assertEqual(index["current"]["version"], "1.2.0")
            self.assertEqual([item["version"] for item in index["releases"]], ["1.2.0"])

    def test_rejects_invalid_or_missing_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_settings(Path(temp_dir))
            with self.assertRaises(HTTPException) as invalid:
                delete_release(settings, "../keys")
            self.assertEqual(invalid.exception.status_code, 400)

            settings.release_output_root.mkdir()
            with self.assertRaises(HTTPException) as missing:
                delete_release(settings, "9.9.9")
            self.assertEqual(missing.exception.status_code, 404)

    def test_restores_release_when_index_write_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self.make_settings(Path(temp_dir))
            folder = self.add_release(settings, "1.2.3")

            with mock.patch(
                "app.release_service.write_release_index",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(HTTPException) as caught:
                    delete_release(settings, "1.2.3")

            self.assertEqual(caught.exception.status_code, 500)
            self.assertTrue(folder.is_dir())


if __name__ == "__main__":
    unittest.main()
