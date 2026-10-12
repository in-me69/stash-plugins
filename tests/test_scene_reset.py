import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "SceneReset"))

import scene_reset  # noqa: E402
from scene_reset import RESET_FIELDS, parse_scene_ids  # noqa: E402


class ParseSceneIdsTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_scene_ids("1,2, 3"), ["1", "2", "3"])
        self.assertEqual(parse_scene_ids("4 5"), ["4", "5"])
        self.assertEqual(parse_scene_ids(["6", 7, "6"]), ["6", "7"])
        self.assertEqual(parse_scene_ids(8), ["8"])

    def test_nothing_or_junk(self):
        for value in (None, "", [], "abc", ["x", ""]):
            self.assertEqual(parse_scene_ids(value), [], value)


class FakeStash(BaseHTTPRequestHandler):
    calls = []
    fail_ids = set()
    plugins = []  # [{"id", "enabled"}]
    scene_paths = {}  # id -> video path, for Folder Media's findScene
    generated = ""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body.get("variables") or {}
        if "input" in v:  # Folder Media's code names its variable "input"
            v = {"i": v["input"]}
        FakeStash.calls.append((q, v))
        if "plugins" in q:
            payload = {"data": {"plugins": FakeStash.plugins}}
        elif "configuration" in q:
            payload = {"data": {"configuration": {"general": {
                "videoExtensions": ["mp4"], "imageExtensions": ["jpg"],
                "generatedPath": FakeStash.generated, "videoFileNamingAlgorithm": "OSHASH"}}}}
        elif "findScene" in q:
            payload = {"data": {"findScene": {"id": v["id"], "files": [
                {"path": FakeStash.scene_paths[v["id"]], "fingerprints": [{"type": "oshash", "value": "h" + v["id"]}]}]}}}
        elif "sceneUpdate" in q and v["i"]["id"] in FakeStash.fail_ids:
            payload = {"errors": [{"message": "scene not found"}]}
        elif "sceneUpdate" in q:
            payload = {"data": {"sceneUpdate": {"id": v["i"]["id"]}}}
        elif "metadataGenerate" in q:
            payload = {"data": {"metadataGenerate": "42"}}
        else:
            raise AssertionError(q)
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class PluginRunTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), FakeStash)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        FakeStash.calls, FakeStash.fail_ids, FakeStash.plugins, FakeStash.scene_paths = [], set(), [], {}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        FakeStash.generated = os.path.join(self.tmp.name, "generated")

    def run_plugin(self, args):
        plugin_input = {"server_connection": {"Host": "0.0.0.0", "Port": self.server.server_port}, "args": args}
        stdout, stderr = io.StringIO(), io.StringIO()
        sys_stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(plugin_input))
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                scene_reset.main()
        finally:
            sys.stdin = sys_stdin
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def updates(self):
        return [v["i"] for q, v in FakeStash.calls if "sceneUpdate" in q and "cover_image" not in v["i"]]

    def cover_updates(self):
        return [v["i"]["id"] for q, v in FakeStash.calls if "sceneUpdate" in q and "cover_image" in v["i"]]

    def make_scene(self, scene_id, *extra_files):
        folder = os.path.join(self.tmp.name, "library", scene_id)
        os.makedirs(folder)
        for name in ("Scene.mp4",) + extra_files:
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"x")
        FakeStash.scene_paths[scene_id] = os.path.join(folder, "Scene.mp4")

    def generates(self):
        return [v["i"] for q, v in FakeStash.calls if "metadataGenerate" in q]

    def test_resets_each_scene_then_regenerates_covers(self):
        result, stderr = self.run_plugin({"scene_ids": "3,5"})
        self.assertEqual(self.updates(), [{"id": "3", **RESET_FIELDS}, {"id": "5", **RESET_FIELDS}])
        self.assertEqual(self.generates(), [{"covers": True, "overwrite": True, "sceneIDs": ["3", "5"]}])
        self.assertEqual(result["output"], "Reset 2 of 2 scenes; cover generation queued for 2")
        self.assertIn("\x01p\x021.0", stderr)

    def test_clears_everything_identify_sets(self):
        cleared = RESET_FIELDS
        for field in ("title", "code", "details", "director"):
            self.assertEqual(cleared[field], "")
        for field in ("urls", "performer_ids", "tag_ids", "groups", "stash_ids"):
            self.assertEqual(cleared[field], [])
        for field in ("date", "production_date", "studio_id"):
            self.assertIsNone(cleared[field])
        self.assertIs(cleared["organized"], False)
        # Kept: things a reset shouldn't touch.
        for field in ("rating100", "gallery_ids", "play_count", "o_counter", "cover_image"):
            self.assertNotIn(field, cleared)

    def test_without_scene_ids_does_nothing(self):
        for args in ({}, {"scene_ids": ""}, {"scene_ids": "not-an-id"}):
            result, stderr = self.run_plugin(args)
            self.assertEqual(FakeStash.calls, [])
            self.assertIn("No scenes given", result["output"])

    def test_failed_scene_is_skipped_and_others_continue(self):
        FakeStash.fail_ids = {"3"}
        result, stderr = self.run_plugin({"scene_ids": ["3", "5"]})
        self.assertEqual(self.generates()[0]["sceneIDs"], ["5"])
        self.assertEqual(result["output"], "Reset 1 of 2 scenes; cover generation queued for 1")
        self.assertIn("\x01w\x02[SceneReset] Scene 3: couldn't reset", stderr)

    def test_no_generate_when_nothing_was_reset(self):
        FakeStash.fail_ids = {"3"}
        result, _ = self.run_plugin({"scene_ids": "3"})
        self.assertEqual(self.generates(), [])
        self.assertEqual(result["output"], "Reset 0 of 1 scenes")

    def test_folder_covers_restored_when_folder_media_enabled(self):
        FakeStash.plugins = [{"id": "FolderMedia", "enabled": True}]
        self.make_scene("3", "cover.jpg", "teaser.mp4")
        self.make_scene("5")  # no folder media: gets a generated cover
        result, _ = self.run_plugin({"scene_ids": "3,5"})
        self.assertEqual(len(self.updates()), 2)  # both reset first
        self.assertEqual(self.cover_updates(), ["3"])
        self.assertEqual(self.generates(), [{"covers": True, "overwrite": True, "sceneIDs": ["5"]}])
        self.assertTrue(os.path.exists(os.path.join(FakeStash.generated, "screenshots", "h3.mp4")))
        self.assertEqual(result["output"], "Reset 2 of 2 scenes; 1 folder covers restored; cover generation queued for 1")

    def test_no_generate_when_every_scene_has_a_folder_cover(self):
        FakeStash.plugins = [{"id": "FolderMedia", "enabled": True}]
        self.make_scene("3", "poster.jpg")
        self.run_plugin({"scene_ids": "3"})
        self.assertEqual(self.cover_updates(), ["3"])
        self.assertEqual(self.generates(), [])

    def test_folder_media_disabled_or_absent_means_generated_covers(self):
        self.make_scene("3", "cover.jpg")
        for plugins in ([], [{"id": "FolderMedia", "enabled": False}]):
            FakeStash.calls = []
            FakeStash.plugins = plugins
            self.run_plugin({"scene_ids": "3"})
            self.assertEqual(self.cover_updates(), [], plugins)
            self.assertEqual(self.generates()[0]["sceneIDs"], ["3"], plugins)

    def test_enabled_but_files_missing_falls_back_to_generating(self):
        FakeStash.plugins = [{"id": "FolderMedia", "enabled": True}]
        self.make_scene("3", "cover.jpg")
        lonely = os.path.join(self.tmp.name, "plugins", "SceneReset")
        os.makedirs(lonely)
        stash = scene_reset.Stash({"Host": "127.0.0.1", "Port": self.server.server_port})
        with redirect_stderr(io.StringIO()) as stderr:
            summary = scene_reset.reset_scenes(stash, ["3"], True, {}, plugin_dir=lonely)
        self.assertEqual(self.generates()[0]["sceneIDs"], ["3"])
        self.assertIn("weren't found", stderr.getvalue())
        self.assertEqual(summary, "Reset 1 of 1 scenes; cover generation queued for 1")

    def test_finds_folder_media_one_level_down(self):
        root = os.path.join(self.tmp.name, "plugins")
        os.makedirs(os.path.join(root, "community", "FolderMedia"))
        with open(os.path.join(root, "community", "FolderMedia", "folder_media.py"), "w") as f:
            f.write("MARKER = 1\n")
        os.makedirs(os.path.join(root, "SceneReset"))
        module = scene_reset.find_folder_media_module(os.path.join(root, "SceneReset"))
        self.assertEqual(module.MARKER, 1)

    def test_cover_regeneration_can_be_turned_off(self):
        self.run_plugin({"scene_ids": "3", "regenerate_covers": "false"})
        self.assertEqual(self.generates(), [])


if __name__ == "__main__":
    unittest.main()
