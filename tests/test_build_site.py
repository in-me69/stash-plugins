"""Tests for build_site.py, which builds the package indexes Stash installs from."""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import build_site  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None


def tracked_files(folder):
    out = subprocess.run(["git", "ls-files", "--", folder], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return sorted(os.path.relpath(p, folder).replace(os.sep, "/") for p in out.splitlines())


class BuildSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "site")
        with redirect_stdout(io.StringIO()):
            build_site.main(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def index(self, kind):
        path = os.path.join(self.out, "index.yml") if kind == "plugins" else os.path.join(self.out, "scrapers", "index.yml")
        with open(path, encoding="utf-8") as f:
            return f.read()

    @unittest.skipUnless(yaml, "needs PyYAML")
    def test_indexes_list_every_package(self):
        for kind, folder_root, outdir in (("plugins", "plugins", self.out),
                                          ("scrapers", "scrapers", os.path.join(self.out, "scrapers"))):
            entries = yaml.safe_load(self.index(kind))
            expected = sorted(d for d in os.listdir(os.path.join(ROOT, folder_root))
                              if os.path.isfile(os.path.join(ROOT, folder_root, d, f"{d}.yml")))
            self.assertEqual(sorted(e["id"] for e in entries), expected, kind)
            for e in entries:
                with self.subTest(package=e["id"]):
                    self.assertEqual(set(e) - {"metadata", "requires"},
                                     {"id", "name", "version", "date", "path", "sha256"})
                    self.assertRegex(e["date"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
                    with open(os.path.join(outdir, e["path"]), "rb") as f:
                        self.assertEqual(e["sha256"], hashlib.sha256(f.read()).hexdigest())

    @unittest.skipUnless(yaml, "needs PyYAML")
    def test_versions_come_from_the_package_files(self):
        entries = {e["id"]: e for e in yaml.safe_load(self.index("plugins")) + yaml.safe_load(self.index("scrapers"))}
        for package_id, path in (("FolderMedia", "plugins/FolderMedia/FolderMedia.yml"),
                                 ("FilenameParser", "scrapers/FilenameParser/FilenameParser.yml")):
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                text = f.read()
            number = build_site.top_level_value(text, "version") or build_site.comment_value(text, "Version")
            self.assertRegex(entries[package_id]["version"], rf"^{number}-[0-9a-f]{{7,}}$")

    def test_zips_hold_exactly_the_tracked_files(self):
        for folder, zip_path in (("plugins/SceneReset", "SceneReset.zip"),
                                 ("scrapers/FilenameParser", "scrapers/FilenameParser.zip")):
            with zipfile.ZipFile(os.path.join(self.out, zip_path)) as z:
                self.assertEqual(sorted(z.namelist()), tracked_files(folder), folder)

    def test_untracked_files_are_not_published(self):
        stray = os.path.join(ROOT, "scrapers", "FilenameParser", "config.ini")
        existed = os.path.exists(stray)
        if not existed:
            with open(stray, "w") as f:
                f.write("[stash]\napi_key = secret\n")
        try:
            with tempfile.TemporaryDirectory() as out, redirect_stdout(io.StringIO()):
                build_site.main(out)
                with zipfile.ZipFile(os.path.join(out, "scrapers", "FilenameParser.zip")) as z:
                    self.assertNotIn("config.ini", z.namelist())
        finally:
            if not existed:
                os.remove(stray)

    def test_builds_are_reproducible(self):
        with tempfile.TemporaryDirectory() as out, redirect_stdout(io.StringIO()):
            build_site.main(out)
            for name in ("index.yml", "FolderMedia.zip", os.path.join("scrapers", "index.yml")):
                with open(os.path.join(out, name), "rb") as a, open(os.path.join(self.out, name), "rb") as b:
                    self.assertEqual(a.read(), b.read(), name)

    @unittest.skipUnless(yaml, "needs PyYAML")
    def test_awkward_values_stay_valid_yaml(self):
        entry = {"id": "X", "name": 'Quote "this": now', "metadata": {"description": "a: b # c\n'd'"},
                 "version": "1.0-abc", "date": "2026-01-01 00:00:00", "path": "X.zip", "sha256": "0",
                 "requires": ["A", "B"]}
        self.assertEqual(yaml.safe_load(build_site.render_index([entry])), [entry])

    def test_reading_values_from_yml(self):
        text = 'name: "Quoted: name"\nversion: 1.2.3\n# requires: A, B\n# Version: 9.9.9\n  name: nested\n'
        self.assertEqual(build_site.top_level_value(text, "name"), "Quoted: name")
        self.assertEqual(build_site.top_level_value(text, "version"), "1.2.3")
        self.assertEqual(build_site.comment_value(text, "requires"), "A, B")
        self.assertEqual(build_site.comment_value(text, "Version"), "9.9.9")
        self.assertIsNone(build_site.top_level_value(text, "description"))


if __name__ == "__main__":
    unittest.main()
