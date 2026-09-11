from __future__ import annotations

import unittest

from app.main import render_dashboard, render_release_overview


class ReleaseOverviewTests(unittest.TestCase):
    def test_publish_form_loads_manifest_fields_from_zip(self):
        markup = render_dashboard().body.decode("utf-8")
        self.assertIn('id="manifest_preview"', markup)
        self.assertIn('fetch("/releases/inspect"', markup)
        self.assertIn('response.status === 413', markup)
        self.assertIn('client_max_body_size', markup)
        self.assertIn('contentType.includes("application/json")', markup)
        self.assertIn('id="publish_button" type="submit" disabled', markup)
        self.assertNotIn('name="version"', markup)
        self.assertNotIn('name="minimum_version"', markup)
        self.assertNotIn('name="notes_json"', markup)

    def test_empty_updater_has_clear_message(self):
        markup = render_release_overview({"current": None, "releases": []})
        self.assertIn("zatím není publikovaná žádná verze", markup)

    def test_current_and_available_versions_are_rendered(self):
        current = {
            "version": "1.46.5",
            "release_date": "2026-09-05",
            "minimum_version": "1.34.4",
            "mandatory": False,
            "summary": "Servisní vydání",
            "byte_size": 3412314,
            "package_url": "/release-files/1.46.5/HADS_Update_1.46.5.zip",
            "current": True,
        }
        previous = {
            "version": "1.46.4",
            "release_date": "2026-09-05",
            "minimum_version": "1.34.4",
            "mandatory": True,
            "byte_size": 1024,
            "package_url": "/release-files/1.46.4/HADS_Update_1.46.4.zip",
            "current": False,
        }
        markup = render_release_overview(
            {"current": current, "releases": [current, previous]},
            csrf_token="test-csrf-token",
        )
        self.assertIn("Aktuální verze", markup)
        self.assertIn("1.46.5", markup)
        self.assertIn("1.46.4", markup)
        self.assertIn("Dostupné verze (2)", markup)
        self.assertIn("3.3 MB", markup)
        self.assertIn("Povinná", markup)
        self.assertIn(current["package_url"], markup)
        self.assertIn('/releases/1.46.5/delete', markup)
        self.assertIn('value="test-csrf-token"', markup)
        self.assertEqual(markup.count('>Smazat</button>'), 2)
        self.assertIn('return confirm(', markup)

    def test_release_values_are_html_escaped(self):
        unsafe = {
            "version": "<script>",
            "release_date": "2026-09-05",
            "minimum_version": "1.0.0",
            "mandatory": False,
            "summary": "<img src=x onerror=alert(1)>",
            "byte_size": 1,
            "package_url": 'javascript:alert("x")',
            "current": True,
        }
        markup = render_release_overview({"current": unsafe, "releases": [unsafe]})
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<img", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("&quot;x&quot;", markup)


if __name__ == "__main__":
    unittest.main()
