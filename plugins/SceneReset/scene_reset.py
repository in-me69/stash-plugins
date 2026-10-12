"""Stash plugin: reset scenes, removing the metadata Identify (or anything
else) added, and regenerate their covers.

Run from the scene list: select scenes, then "Reset metadata..." in the list's
menu (added by scene_reset.js), which queues this plugin's task with the
selected scene IDs. Without scene IDs the task does nothing, so it can never
reset the whole library by accident.

Cleared: title, code, details, director, URLs, date, production date, studio,
performers, tags, groups, stash-box IDs and the organized flag. Files, play
history, O-count, rating, markers and galleries are kept. Performers,
studios and tags themselves are never deleted, only removed from the scene.

Covers: if the Folder Media plugin is installed and enabled, its rules are
applied to each reset scene first, so a cover/poster image next to the video
becomes the cover again (and a teaser is re-copied as the preview). Only
scenes without a folder cover get a newly generated screenshot.

Standard library only, so it runs on whatever Python Stash is configured with.
"""

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request

# What a reset scene is set to. Empty lists and None clear a field.
RESET_FIELDS = {
    "title": "",
    "code": "",
    "details": "",
    "director": "",
    "urls": [],
    "date": None,
    "production_date": None,
    "studio_id": None,
    "performer_ids": [],
    "tag_ids": [],
    "groups": [],
    "stash_ids": [],
    "organized": False,
}


def log(message, level="info"):
    """Writes to Stash's log; the "\\x01<level>\\x02" prefix sets the level
    (trace, debug, info, warning, error)."""
    prefix = f"\x01{level[0]}\x02"
    for line in str(message).splitlines() or [""]:
        print(f"{prefix}[SceneReset] {line}", file=sys.stderr, flush=True)


def progress(fraction):
    # Stash shows a "\x01p\x02<0..1>" line as the task's progress.
    print(f"\x01p\x02{fraction}", file=sys.stderr, flush=True)


class Stash:
    def __init__(self, server_connection):
        """server_connection is what Stash passes every plugin: scheme, host,
        port and, when Stash has a login, a session cookie to authenticate
        with."""
        scheme = server_connection.get("Scheme") or "http"
        host = server_connection.get("Host") or "localhost"
        if host in ("0.0.0.0", "::", "[::]", ""):
            host = "localhost"  # listening on all interfaces; reach it locally
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = server_connection.get("Port") or 9999
        self.endpoint = f"{scheme}://{host}:{port}/graphql"
        cookie = server_connection.get("SessionCookie") or {}
        self.cookie = f"{cookie['Name']}={cookie['Value']}" if cookie.get("Name") else ""

    def query(self, query, variables=None):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(self.endpoint, data=body, headers=headers)
        context = None
        if self.endpoint.startswith("https://localhost"):
            import ssl
            # Stash's own HTTPS cert is usually self-signed; it's the local machine.
            context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL error: {payload['errors']}")
        return payload["data"]


FOLDER_MEDIA_ID = "FolderMedia"
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def find_folder_media_module(plugin_dir=PLUGIN_DIR):
    """Loads the Folder Media plugin's code if it's installed alongside this
    one: in a sibling folder, or one level further down (e.g. a "community"
    folder of the plugins directory). Returns the module, or None."""
    plugins_root = os.path.dirname(plugin_dir)
    candidates = [os.path.join(plugins_root, FOLDER_MEDIA_ID, "folder_media.py")]
    try:
        for entry in os.scandir(plugins_root):
            if entry.is_dir():
                candidates.append(os.path.join(entry.path, FOLDER_MEDIA_ID, "folder_media.py"))
    except OSError:
        pass
    for path in candidates:
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("folder_media", path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:  # a broken copy shouldn't stop the reset
                log(f"Couldn't load Folder Media from {path}: {e}", "warning")
                return None
            return module
    return None


def folder_media_enabled(stash):
    try:
        plugins = stash.query("{ plugins { id enabled } }").get("plugins") or []
    except (RuntimeError, urllib.error.URLError):
        return False
    return any(p["id"] == FOLDER_MEDIA_ID and p["enabled"] for p in plugins)


def apply_folder_covers(server_connection, stash, scene_ids, plugin_dir=PLUGIN_DIR):
    """Runs Folder Media's rules on the scenes, if that plugin is installed
    and enabled. Returns the IDs of scenes that got a cover from it."""
    if not folder_media_enabled(stash):
        return set()
    folder_media = find_folder_media_module(plugin_dir)
    if not folder_media:
        log("Folder Media is enabled but its files weren't found next to this plugin; "
            "generating covers instead", "warning")
        return set()
    fm_stash = folder_media.Stash(server_connection)
    settings = fm_stash.settings()
    covered = set()
    for scene_id in scene_ids:
        try:
            scene = fm_stash.scene(scene_id)
            done = folder_media.apply_folder_media(fm_stash, settings, scene) if scene else []
        except Exception as e:
            log(f"Scene {scene_id}: Folder Media failed, generating a cover instead: {e}", "warning")
            continue
        if any(d.startswith("cover") for d in done):
            covered.add(scene_id)
    return covered


def parse_scene_ids(value):
    """Scene IDs from the task argument: a list, or a comma/space separated
    string ("1, 2 3")."""
    if value is None:
        return []
    if isinstance(value, (int, float)):
        value = [value]
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    ids = []
    for v in value:
        v = str(v).strip()
        if v.isdigit() and v not in ids:
            ids.append(v)
    return ids


def reset_scenes(stash, scene_ids, regenerate_covers=True, server_connection=None, plugin_dir=PLUGIN_DIR):
    """Clears the scenes' metadata, then restores folder covers (via Folder
    Media, if enabled) and queues cover generation for the rest. Returns a
    summary string."""
    reset = []
    for i, scene_id in enumerate(scene_ids):
        try:
            stash.query(
                "mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
                {"i": {"id": scene_id, **RESET_FIELDS}},
            )
            reset.append(scene_id)
            log(f"Scene {scene_id}: metadata reset")
        except (RuntimeError, urllib.error.URLError) as e:
            log(f"Scene {scene_id}: couldn't reset: {e}", "warning")
        progress((i + 1) / len(scene_ids))

    summary = f"Reset {len(reset)} of {len(scene_ids)} scenes"
    if reset and regenerate_covers:
        folder_covered = apply_folder_covers(server_connection or {}, stash, reset, plugin_dir)
        if folder_covered:
            summary += f"; {len(folder_covered)} folder covers restored"
        to_generate = [i for i in reset if i not in folder_covered]
        if to_generate:
            # Queued as Stash's own Generate job; overwrite replaces the current cover.
            stash.query(
                "mutation($i: GenerateMetadataInput!) { metadataGenerate(input: $i) }",
                {"i": {"covers": True, "overwrite": True, "sceneIDs": to_generate}},
            )
            summary += f"; cover generation queued for {len(to_generate)}"
    log(summary)
    return summary


def main():
    plugin_input = json.loads(sys.stdin.read() or "{}")
    args = plugin_input.get("args") or {}
    scene_ids = parse_scene_ids(args.get("scene_ids"))
    if not scene_ids:
        output = "No scenes given. Select scenes in the scene list and use \"Reset metadata...\" from its menu."
        log(output, "warning")
    else:
        server_connection = plugin_input.get("server_connection") or {}
        stash = Stash(server_connection)
        regenerate = str(args.get("regenerate_covers", "true")).lower() not in ("false", "0", "no")
        output = reset_scenes(stash, scene_ids, regenerate, server_connection)
    print(json.dumps({"output": output}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report to Stash rather than dying with a traceback
        log(f"{type(e).__name__}: {e}", "error")
        print(json.dumps({"error": str(e)}))
