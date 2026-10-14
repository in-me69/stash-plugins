"""Every scraper/plugin definition must be valid YAML: Stash silently skips
(or refuses to load) a file it can't parse."""
import glob
import os
import unittest

try:
    import yaml
except ImportError:  # CI installs it; locally it's optional
    yaml = None

ROOT = os.path.join(os.path.dirname(__file__), "..")


@unittest.skipUnless(yaml, "needs PyYAML")
class YamlFilesTest(unittest.TestCase):
    def test_definitions_parse(self):
        files = glob.glob(os.path.join(ROOT, "scrapers", "*", "*.yml")) + \
            glob.glob(os.path.join(ROOT, "plugins", "*", "*.yml"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=os.path.relpath(path, ROOT)):
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                self.assertIsInstance(data.get("name"), str)
                if "/plugins/" in path.replace(os.sep, "/"):
                    script = data["exec"][-1].replace("{pluginDir}/", "")
                    self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(path), script)), script)
                    for setting in (data.get("settings") or {}).values():
                        self.assertIn(setting["type"], ("STRING", "NUMBER", "BOOLEAN"))
