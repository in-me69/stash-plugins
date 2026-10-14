import base64
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "FolderMedia"))

import folder_media  # noqa: E402
from folder_media import NamePatterns, find_folder_media  # noqa: E402

VIDEO = ["mp4", "mkv", "webm"]
IMAGE = ["jpg", "jpeg", "png", "webp"]


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "library")
        os.makedirs(self.dir)

    def touch(self, *names, content=b"x"):
        for name in names:
            path = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)
        return os.path.join(self.dir, names[0])

    def path(self, name):
        return os.path.join(self.dir, name)

    def find(self, video):
        with redirect_stderr(io.StringIO()):
            return find_folder_media(video, VIDEO, IMAGE)


class FindCoverTest(FolderTest):
    def test_cover_and_poster_any_case_and_extension(self):
        for image in ("cover.jpg", "Poster.PNG", "COVER.webp", "poster.jpeg"):
            with self.subTest(image=image):
                for name in os.listdir(self.dir):
                    os.remove(self.path(name))
                video = self.touch("Scene.mp4", image)
                self.assertEqual(self.find(video), (self.path(image), None))

    def test_cover_preferred_over_poster(self):
        video = self.touch("Scene.mp4", "poster.jpg", "cover.png")
        self.assertEqual(self.find(video)[0], self.path("cover.png"))

    def test_only_when_the_only_video_in_the_folder(self):
        video = self.touch("Part 1.mp4", "Part 2.mkv", "cover.jpg", "teaser.mp4")
        self.assertEqual(self.find(video), (None, None))

    def test_videos_in_subfolders_dont_count(self):
        video = self.touch("Scene.mp4", "cover.jpg", "extras/Trailer.mp4")
        self.assertEqual(self.find(video), (self.path("cover.jpg"), None))

    def test_images_in_subfolders_dont_count(self):
        video = self.touch("Scene.mp4", "assets/cover.jpg")
        self.assertEqual(self.find(video), (None, None))

    def test_other_image_names_and_non_images_ignored(self):
        video = self.touch("Scene.mp4", "screenshot.jpg", "cover.txt", "my cover.jpg", "poster.nfo")
        self.assertEqual(self.find(video), (None, None))


class FindPreviewTest(FolderTest):
    def test_teaser_preview_trailer_any_case_and_video_extension(self):
        for preview in ("teaser.mp4", "Preview.MKV", "TRAILER.webm"):
            with self.subTest(preview=preview):
                for name in os.listdir(self.dir):
                    os.remove(self.path(name))
                video = self.touch("Scene.mp4", preview)
                self.assertEqual(self.find(video), (None, self.path(preview)))

    def test_name_order_teaser_then_preview_then_trailer(self):
        video = self.touch("Scene.mp4", "trailer.mp4", "preview.mp4", "teaser.mp4")
        self.assertEqual(self.find(video)[1], self.path("teaser.mp4"))
        os.remove(self.path("teaser.mp4"))
        self.assertEqual(self.find(video)[1], self.path("preview.mp4"))

    def test_browser_friendly_extension_preferred(self):
        video = self.touch("Scene.mp4", "teaser.mkv", "teaser.mp4")
        self.assertEqual(self.find(video)[1], self.path("teaser.mp4"))

    def test_teasers_dont_count_as_a_second_video(self):
        video = self.touch("Scene.mp4", "teaser.mp4", "cover.jpg")
        self.assertEqual(self.find(video), (self.path("cover.jpg"), self.path("teaser.mp4")))

    def test_teaser_imported_as_its_own_scene_is_left_alone(self):
        self.touch("Scene.mp4", "cover.jpg")
        self.assertEqual(self.find(self.touch("teaser.mp4")), (None, None))


class NamePatternsTest(unittest.TestCase):
    def rank(self, text, name):
        return NamePatterns(text, "cover").rank(name)

    def test_plain_names_in_order_ignoring_case_and_extension(self):
        self.assertEqual(self.rank("cover, poster", "POSTER.jpg"), 1)
        self.assertEqual(self.rank("cover, poster", "/a/b/Cover.PNG"), 0)
        self.assertIsNone(self.rank("cover, poster", "my cover.jpg"))  # whole name must match

    def test_regular_expressions(self):
        self.assertEqual(self.rank(r"folder, .*[-_ ]cover", "Scene_Cover.jpg"), 1)
        self.assertEqual(self.rank(r"fanart\d?", "fanart2.jpg"), 0)

    def test_commas_inside_repeat_counts_dont_split(self):
        patterns = NamePatterns(r"cover\d{1,2}, poster", "cover")
        self.assertEqual(len(patterns.patterns), 2)
        self.assertEqual(patterns.rank("cover12.jpg"), 0)

    def test_invalid_or_empty_falls_back_to_default(self):
        with redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(NamePatterns("cover(", "poster").rank("poster.jpg"), 0)
        self.assertIn("Invalid name pattern", stderr.getvalue())
        self.assertEqual(NamePatterns(" , ", "poster").rank("poster.jpg"), 0)


class CustomNamesTest(FolderTest):
    def find(self, video, cover, preview):
        with redirect_stderr(io.StringIO()):
            return find_folder_media(video, VIDEO, IMAGE, NamePatterns(cover, "cover"), NamePatterns(preview, "teaser"))

    def test_custom_cover_names(self):
        video = self.touch("Scene.mp4", "cover.jpg", "folder.jpg", "Scene-fanart.jpg")
        self.assertEqual(self.find(video, "folder, cover", "teaser")[0], self.path("folder.jpg"))
        self.assertEqual(self.find(video, r".*-fanart", "teaser")[0], self.path("Scene-fanart.jpg"))

    def test_custom_preview_names_dont_count_as_a_second_video(self):
        video = self.touch("Scene.mp4", "sample.mp4")
        self.assertEqual(self.find(video, "cover", "teaser"), (None, None))  # two videos
        self.assertEqual(self.find(video, "cover", "sample"), (None, self.path("sample.mp4")))


class FakeStash(BaseHTTPRequestHandler):
    """Answers the plugin's GraphQL queries from class-level state and
    records sceneUpdate calls."""
    scenes = {}  # id -> (path, {fingerprint type: value})
    generated = ""
    algorithm = "OSHASH"
    plugin_config = {}
    updates = []
    cookies = []

    @classmethod
    def scene_json(cls, scene_id):
        path, fingerprints = cls.scenes[scene_id]
        return {"id": scene_id, "files": [{
            "path": path, "fingerprints": [{"type": t, "value": v} for t, v in fingerprints.items()],
        }]}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeStash.cookies.append(self.headers.get("Cookie"))
        query, variables = body["query"], body.get("variables") or {}
        if "plugins(" in query:
            data = {"configuration": {"plugins": FakeStash.plugin_config}}
        elif "configuration" in query:
            data = {"configuration": {"general": {
                "videoExtensions": VIDEO, "imageExtensions": IMAGE,
                "generatedPath": FakeStash.generated, "videoFileNamingAlgorithm": FakeStash.algorithm,
            }}}
        elif "findScenes" in query:
            data = {"findScenes": {"scenes": [FakeStash.scene_json(i) for i in FakeStash.scenes]}}
        elif "findScene" in query:
            data = {"findScene": FakeStash.scene_json(variables["id"])}
        elif "sceneUpdate" in query:
            FakeStash.updates.append(variables["input"])
            data = {"sceneUpdate": {"id": variables["input"]["id"]}}
        else:
            data = None
        out = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


class PluginRunTest(FolderTest):
    """Runs the plugin's main() the way Stash does: JSON on stdin, result on stdout."""

    def setUp(self):
        super().setUp()
        self.server = HTTPServer(("127.0.0.1", 0), FakeStash)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # Cleanups run last-in-first-out: stop serving, then close the socket.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.generated = os.path.join(self.tmp.name, "generated")
        FakeStash.scenes, FakeStash.updates, FakeStash.cookies = {}, [], []
        FakeStash.generated, FakeStash.algorithm = self.generated, "OSHASH"
        FakeStash.plugin_config = {}

    def run_plugin(self, args, server_connection=None):
        plugin_input = {
            "server_connection": server_connection or {
                "Scheme": "http", "Host": "0.0.0.0", "Port": self.server.server_port,
                "SessionCookie": {"Name": "session", "Value": "abc"},
            },
            "args": args,
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        sys_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(plugin_input))
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                folder_media.main()
        finally:
            sys.stdin = sys_stdin
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def preview_file(self, file_hash):
        return os.path.join(self.generated, "screenshots", f"{file_hash}.mp4")

    def test_hook_sets_cover_and_preview_for_new_scene(self):
        video = self.touch("one/Scene.mp4")
        self.touch("one/poster.png", content=b"PNGDATA")
        self.touch("one/teaser.mp4", content=b"TEASER")
        FakeStash.scenes = {"7": (video, {"oshash": "abc123", "md5": "ffff"})}
        result, _ = self.run_plugin({"hookContext": {"id": 7, "type": "Scene.Create.Post"}})
        self.assertEqual(FakeStash.updates, [{
            "id": "7", "cover_image": "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode(),
        }])
        with open(self.preview_file("abc123"), "rb") as f:
            self.assertEqual(f.read(), b"TEASER")
        self.assertIn("poster.png", result["output"])
        self.assertIn("teaser.mp4", result["output"])
        self.assertTrue(all(c == "session=abc" for c in FakeStash.cookies))
        # No temporary files left behind next to the preview.
        self.assertEqual(os.listdir(os.path.dirname(self.preview_file("abc123"))), ["abc123.mp4"])

    def test_preview_named_by_md5_when_stash_uses_md5(self):
        FakeStash.algorithm = "MD5"
        video = self.touch("one/Scene.mp4", "one/teaser.mp4")
        FakeStash.scenes = {"7": (video, {"oshash": "abc123", "md5": "ffff"})}
        self.run_plugin({"hookContext": {"id": 7}})
        self.assertTrue(os.path.exists(self.preview_file("ffff")))
        self.assertFalse(os.path.exists(self.preview_file("abc123")))

    def test_relative_generated_path_is_relative_to_stash_config(self):
        FakeStash.generated = "generated"
        video = self.touch("one/Scene.mp4", "one/teaser.mp4")
        FakeStash.scenes = {"7": (video, {"oshash": "abc123"})}
        self.run_plugin({"hookContext": {"id": 7}}, {
            "Host": "127.0.0.1", "Port": self.server.server_port,
            "Dir": os.path.join(self.tmp.name, "config.yml"),
        })
        self.assertTrue(os.path.exists(self.preview_file("abc123")))

    def test_existing_preview_is_replaced(self):
        video = self.touch("one/Scene.mp4")
        self.touch("one/teaser.mp4", content=b"NEW")
        os.makedirs(os.path.dirname(self.preview_file("abc123")))
        with open(self.preview_file("abc123"), "wb") as f:
            f.write(b"GENERATED")
        FakeStash.scenes = {"7": (video, {"oshash": "abc123"})}
        self.run_plugin({"hookContext": {"id": 7}})
        with open(self.preview_file("abc123"), "rb") as f:
            self.assertEqual(f.read(), b"NEW")

    def test_missing_hash_skips_preview_but_still_sets_cover(self):
        video = self.touch("one/Scene.mp4", "one/teaser.mp4", "one/cover.jpg")
        FakeStash.scenes = {"7": (video, {})}
        result, stderr = self.run_plugin({"hookContext": {"id": 7}})
        self.assertEqual(len(FakeStash.updates), 1)
        self.assertFalse(os.path.exists(os.path.join(self.generated, "screenshots")))
        self.assertIn("no oshash hash", stderr)

    def test_hook_leaves_scene_alone_without_folder_media(self):
        FakeStash.scenes = {"7": (self.touch("one/Scene.mp4"), {"oshash": "abc123"})}
        result, _ = self.run_plugin({"hookContext": {"id": 7, "type": "Scene.Create.Post"}})
        self.assertEqual(FakeStash.updates, [])
        self.assertFalse(os.path.exists(self.generated))
        self.assertEqual(result["output"], "No folder media")

    def test_names_from_plugin_settings(self):
        FakeStash.plugin_config = {"FolderMedia": {"coverNames": "folder", "previewNames": "sample"}}
        video = self.touch("one/Scene.mp4", "one/cover.jpg", "one/folder.jpg", "one/teaser.mp4")
        self.touch("one/sample.mp4", content=b"SAMPLE")
        FakeStash.scenes = {"7": (video, {"oshash": "abc123"})}
        # teaser.mp4 is now an ordinary second video, so nothing qualifies...
        result, _ = self.run_plugin({"hookContext": {"id": 7}})
        self.assertEqual(result["output"], "No folder media")
        # ...until it's gone.
        os.remove(self.path("one/teaser.mp4"))
        result, _ = self.run_plugin({"hookContext": {"id": 7}})
        self.assertIn("folder.jpg", result["output"])
        with open(self.preview_file("abc123"), "rb") as f:
            self.assertEqual(f.read(), b"SAMPLE")

    def test_task_updates_every_matching_scene(self):
        a = self.touch("a/A.mp4", "a/cover.jpg", "a/trailer.mp4")
        b = self.touch("b/B.mp4")
        c = self.touch("c/C1.mp4", "c/C2.mp4", "c/cover.jpg", "c/teaser.mp4")
        d = self.touch("d/D.mp4", "d/preview.mp4")
        FakeStash.scenes = {
            "1": (a, {"oshash": "aaa"}), "2": (b, {"oshash": "bbb"}),
            "3": (c, {"oshash": "ccc"}), "4": (d, {"oshash": "ddd"}),
        }
        result, stderr = self.run_plugin({})
        self.assertEqual([u["id"] for u in FakeStash.updates], ["1"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.generated, "screenshots"))), ["aaa.mp4", "ddd.mp4"])
        self.assertEqual(result["output"], "Set 1 covers and 2 previews from folder media")
        self.assertIn("\x01p\x021.0", stderr)  # progress reported to Stash


if __name__ == "__main__":
    unittest.main()
