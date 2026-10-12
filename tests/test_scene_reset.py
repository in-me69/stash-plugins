import io
import json
import os
import sys
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

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body.get("variables") or {}
        FakeStash.calls.append((q, v))
        if "sceneUpdate" in q and v["i"]["id"] in FakeStash.fail_ids:
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
        FakeStash.calls, FakeStash.fail_ids = [], set()

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
        return [v["i"] for q, v in FakeStash.calls if "sceneUpdate" in q]

    def generates(self):
        return [v["i"] for q, v in FakeStash.calls if "metadataGenerate" in q]

    def test_resets_each_scene_then_regenerates_covers(self):
        result, stderr = self.run_plugin({"scene_ids": "3,5"})
        self.assertEqual(self.updates(), [{"id": "3", **RESET_FIELDS}, {"id": "5", **RESET_FIELDS}])
        self.assertEqual(self.generates(), [{"covers": True, "overwrite": True, "sceneIDs": ["3", "5"]}])
        self.assertEqual(result["output"], "Reset 2 of 2 scenes; cover generation queued")
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
        self.assertEqual(result["output"], "Reset 1 of 2 scenes; cover generation queued")
        self.assertIn("\x01w\x02[SceneReset] Scene 3: couldn't reset", stderr)

    def test_no_generate_when_nothing_was_reset(self):
        FakeStash.fail_ids = {"3"}
        result, _ = self.run_plugin({"scene_ids": "3"})
        self.assertEqual(self.generates(), [])
        self.assertEqual(result["output"], "Reset 0 of 1 scenes")

    def test_cover_regeneration_can_be_turned_off(self):
        self.run_plugin({"scene_ids": "3", "regenerate_covers": "false"})
        self.assertEqual(self.generates(), [])


if __name__ == "__main__":
    unittest.main()
