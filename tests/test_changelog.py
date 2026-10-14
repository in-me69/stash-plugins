"""Each scraper's and plugin's version must match the newest entry for it in
CHANGELOG.md, so a version bump without a changelog entry (or the reverse)
fails CI."""
import glob
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def changelog_versions():
    """{section name: newest version}, e.g. {"Folder Media": "0.2.0"}."""
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    versions = {}
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        name = re.sub(r"\s*\((plugin|scraper)\)\s*$", "", section.splitlines()[0]).strip()
        match = re.search(r"^### (\d+\.\d+\.\d+)\b", section, re.M)
        if match:
            versions[name] = match.group(1)
    return versions


def definition_versions():
    """{name: version} from the scraper/plugin YAML files. Plugins have a
    "version:" field; scrapers can't, so they carry a "# Version:" comment."""
    found = {}
    for path in glob.glob(os.path.join(ROOT, "plugins", "*", "*.yml")) + \
            glob.glob(os.path.join(ROOT, "scrapers", "*", "*.yml")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        name = re.search(r"^name:\s*(.+)$", text, re.M).group(1).strip()
        version = re.search(r"^(?:version:|# Version:)\s*(\S+)", text, re.M)
        found[name] = (version.group(1) if version else None, os.path.relpath(path, ROOT))
    return found


class ChangelogTest(unittest.TestCase):
    def test_versions_match_changelog(self):
        logged = changelog_versions()
        definitions = definition_versions()
        self.assertTrue(definitions)
        for name, (version, path) in definitions.items():
            with self.subTest(name=name):
                self.assertIsNotNone(version, f"{path} has no version")
                self.assertIn(name, logged, f"CHANGELOG.md has no section for {name}")
                self.assertEqual(version, logged[name], f"{path} says {version}, CHANGELOG.md's newest is {logged[name]}")

    def test_every_changelog_section_is_a_real_scraper_or_plugin(self):
        self.assertEqual(set(changelog_versions()), set(definition_versions()))


if __name__ == "__main__":
    unittest.main()
