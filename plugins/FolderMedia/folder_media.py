"""Stash plugin: use media that sits next to a video for its scene, instead of
what Stash would generate:

- an image named "cover" or "poster" becomes the scene's cover
- a video named "teaser", "preview" or "trailer" becomes its preview video
  (the clip that plays when hovering over the scene)

When a scan adds a new scene, Stash runs this plugin's Scene.Create.Post hook
before it queues cover and preview generation, and both skip a scene that
already has one. So setting them here means nothing is generated (unless the
scan was run with its overwrite option).

Stash has no API for setting a preview. It stores each scene's preview at
<generated>/screenshots/<file hash>.mp4 and only generates one if that file
is missing, so the teaser is copied there. With the "Shrink previews"
setting on, it's converted with ffmpeg instead: the first few seconds (the
"Preview length" setting), at most 640px wide (Stash's own preview width),
H.264, with sound only if Stash's preview audio setting is on. Any existing
animated .webp preview for the scene is deleted when the preview is
replaced, so Stash's Generate rebuilds it from the new one.

For now this only applies when the video is the only video in its folder
(teasers aside), so the files clearly belong to it. Names are matched
ignoring case and extension; earlier names win. The names can be changed in
the plugin's settings: comma-separated, in order of preference, each a plain
name or a regular expression matched against the whole file name.

A task does the same for existing scenes.

Standard library only, so it runs on whatever Python Stash is configured with.
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

PLUGIN_ID = "FolderMedia"
# Defaults for the plugin's coverNames/previewNames settings, in order of preference.
DEFAULT_COVER_NAMES = "cover, poster"
DEFAULT_PREVIEW_NAMES = "teaser, preview, trailer"
# Browsers play these; used first if a preview exists in several formats.
PREFERRED_PREVIEW_EXTENSIONS = ("mp4", "m4v", "webm")
# Shrinking teasers: Stash's own preview width, and the default length kept.
PREVIEW_WIDTH = 640
DEFAULT_PREVIEW_SECONDS = 10
MAX_PREVIEW_SECONDS = 600
FFMPEG_TIMEOUT_SECONDS = 600

# Used if Stash's own extension lists can't be read.
DEFAULT_VIDEO_EXTENSIONS = [
    "m4v", "mp4", "mov", "wmv", "avi", "mpg", "mpeg", "rmvb", "rm", "flv",
    "asf", "mkv", "webm", "f4v",
]
DEFAULT_IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "avif"]


def log(message, level="info"):
    """Writes to Stash's log; the "\\x01<level>\\x02" prefix sets the level
    (trace, debug, info, warning, error)."""
    prefix = f"\x01{level[0]}\x02"
    for line in str(message).splitlines() or [""]:
        print(f"{prefix}[FolderMedia] {line}", file=sys.stderr, flush=True)


# --- Finding the files ---------------------------------------------------

def _extension(name):
    return os.path.splitext(name)[1][1:].lower()


def _stem(name):
    return os.path.splitext(os.path.basename(name))[0].casefold()


class NamePatterns:
    """An ordered list of file-name patterns, from a setting like
    "cover, poster" or "teaser, .*[-_ ]trailer". Each entry is a regular
    expression matched against the whole name (without extension), ignoring
    case; a plain word just matches itself. Earlier entries are preferred."""

    # Split on commas, except inside a {m,n} repeat count.
    _SPLIT = re.compile(r",(?![^{]*\})")

    def __init__(self, text, default):
        self.text = text
        try:
            self.patterns = self._compile(text)
        except re.error as e:
            log(f"Invalid name pattern {text!r} ({e}); using {default!r}", "warning")
            self.text, self.patterns = default, self._compile(default)
        if not self.patterns:
            self.text, self.patterns = default, self._compile(default)

    @classmethod
    def _compile(cls, text):
        return [re.compile(p.strip(), re.IGNORECASE) for p in cls._SPLIT.split(text or "") if p.strip()]

    def rank(self, name):
        """Index of the first pattern the file name matches, or None."""
        stem = os.path.splitext(os.path.basename(name))[0]
        for i, pattern in enumerate(self.patterns):
            if pattern.fullmatch(stem):
                return i
        return None

    def __repr__(self):
        return f"NamePatterns({self.text!r})"


DEFAULT_COVER_PATTERNS = NamePatterns(DEFAULT_COVER_NAMES, DEFAULT_COVER_NAMES)
DEFAULT_PREVIEW_PATTERNS = NamePatterns(DEFAULT_PREVIEW_NAMES, DEFAULT_PREVIEW_NAMES)


def is_preview_file(path, preview_patterns=DEFAULT_PREVIEW_PATTERNS):
    """Whether a file's name marks it as a teaser/preview/trailer."""
    return preview_patterns.rank(path) is not None


def _pick(entries, patterns, extensions, preferred_extensions=()):
    """The entry whose name matches the earliest of `patterns`, with one of
    `extensions`. Among several files for one pattern, preferred_extensions
    come first, then alphabetical, so the choice is predictable."""
    matches = []
    for e in entries:
        rank = patterns.rank(e.name)
        if rank is not None and _extension(e.name) in extensions:
            ext = _extension(e.name)
            ext_order = preferred_extensions.index(ext) if ext in preferred_extensions else len(preferred_extensions)
            matches.append((rank, ext_order, e.path))
    return min(matches)[2] if matches else None


def find_folder_media(video_path, video_extensions, image_extensions,
                      cover_patterns=DEFAULT_COVER_PATTERNS, preview_patterns=DEFAULT_PREVIEW_PATTERNS):
    """(cover image, preview video) for video_path; either may be None.

    Both are None unless video_path is the only video in its folder, not
    counting teaser/preview/trailer files. Only the folder itself is looked
    at, not subfolders."""
    if is_preview_file(video_path, preview_patterns):
        return None, None  # a teaser imported as its own scene
    folder = os.path.dirname(video_path)
    try:
        entries = [e for e in os.scandir(folder) if e.is_file()]
    except OSError as e:
        log(f"Can't read folder {folder}: {e}", "warning")
        return None, None

    video_extensions = {x.lower().lstrip(".") for x in video_extensions}
    image_extensions = {x.lower().lstrip(".") for x in image_extensions}

    videos = [e for e in entries
              if _extension(e.name) in video_extensions and not is_preview_file(e.name, preview_patterns)]
    if len(videos) != 1:
        return None, None

    cover = _pick(entries, cover_patterns, image_extensions)
    preview = _pick(entries, preview_patterns, video_extensions, PREFERRED_PREVIEW_EXTENSIONS)
    return cover, preview


def find_ffmpeg(configured="", config_dir=""):
    """The ffmpeg Stash uses: its ffmpeg path setting, else ffmpeg on the
    PATH, else the copy Stash downloads into its config folder. None if
    there's none."""
    if configured:
        if os.path.isfile(configured):
            return configured
        found = shutil.which(configured)
        if found:
            return found
    found = shutil.which("ffmpeg")
    if found:
        return found
    for name in ("ffmpeg", "ffmpeg.exe"):
        path = os.path.join(config_dir or "", name)
        if config_dir and os.path.isfile(path):
            return path
    return None


def shrink_command(ffmpeg, source, destination, seconds, audio, preset):
    """ffmpeg arguments turning a teaser into a Stash-sized preview: the
    first `seconds`, at most PREVIEW_WIDTH wide, H.264 MP4 that starts
    playing before it's fully loaded, with AAC sound only if `audio`."""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", source,
        "-t", f"{seconds:g}",
        "-map", "0:v:0",
    ]
    if audio:
        cmd += ["-map", "0:a:0?"]  # "?": fine if the teaser has no sound
    cmd += [
        "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2",
        "-c:v", "libx264", "-preset", preset or "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-movflags", "+faststart",
    ]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]
    return cmd + ["-f", "mp4", destination]


def shrink_into_place(settings, source, destination):
    """Converts source into a small preview at destination, via a temporary
    file so Stash never serves a half-written one. Raises RuntimeError if
    ffmpeg fails."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(destination), suffix=".tmp.mp4")
    os.close(fd)
    try:
        cmd = shrink_command(settings.ffmpeg, source, temp, settings.preview_seconds,
                             settings.preview_audio, settings.preview_preset)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ffmpeg took longer than {FFMPEG_TIMEOUT_SECONDS}s")
        if result.returncode != 0 or not os.path.getsize(temp):
            error = (result.stderr or "").strip().splitlines()[-3:]
            raise RuntimeError(f"ffmpeg failed ({result.returncode}): {' / '.join(error) or 'no output'}")
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def image_data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"


def copy_into_place(source, destination):
    """Copies source to destination via a temporary file in the same folder,
    so Stash never serves a half-copied preview."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(destination), suffix=".tmp")
    os.close(fd)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


# --- Stash GraphQL -------------------------------------------------------

class Settings:
    """The parts of Stash's configuration this plugin needs."""

    def __init__(self, video_extensions, image_extensions, generated_path, hash_algorithm,
                 cover_patterns=DEFAULT_COVER_PATTERNS, preview_patterns=DEFAULT_PREVIEW_PATTERNS,
                 shrink_previews=False, preview_seconds=DEFAULT_PREVIEW_SECONDS, preview_audio=False,
                 preview_preset="veryfast", ffmpeg=None):
        self.video_extensions = video_extensions
        self.image_extensions = image_extensions
        self.generated_path = generated_path
        self.hash_algorithm = hash_algorithm  # "oshash" or "md5"
        self.cover_patterns = cover_patterns
        self.preview_patterns = preview_patterns
        self.shrink_previews = shrink_previews
        self.preview_seconds = preview_seconds
        self.preview_audio = preview_audio  # Stash's own "preview audio" setting
        self.preview_preset = preview_preset  # Stash's own preview encoding preset
        self.ffmpeg = ffmpeg

    def preview_path(self, file_hash):
        return os.path.join(self.generated_path, "screenshots", f"{file_hash}.mp4")

    def webp_path(self, file_hash):
        return os.path.join(self.generated_path, "screenshots", f"{file_hash}.webp")


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _as_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_SECONDS
    if seconds <= 0:
        return DEFAULT_PREVIEW_SECONDS
    return min(seconds, MAX_PREVIEW_SECONDS)


class Stash:
    def __init__(self, server_connection):
        """server_connection is what Stash passes every plugin: scheme, host,
        port, its config folder and, when Stash has a login, a session
        cookie to authenticate with."""
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
        config_dir = server_connection.get("Dir") or ""
        if config_dir.endswith((".yml", ".yaml")):
            config_dir = os.path.dirname(config_dir)
        self.config_dir = config_dir

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

    def settings(self):
        general = self.query(
            "{ configuration { general { videoExtensions imageExtensions generatedPath videoFileNamingAlgorithm"
            " ffmpegPath previewAudio previewPreset } } }"
        )["configuration"]["general"]
        generated = general.get("generatedPath") or ""
        if generated and not os.path.isabs(generated) and self.config_dir:
            generated = os.path.join(self.config_dir, generated)
        plugin = self.plugin_settings()
        shrink = _as_bool(plugin.get("shrinkPreviews", False))
        ffmpeg = find_ffmpeg(general.get("ffmpegPath") or "", self.config_dir) if shrink else None
        if shrink and not ffmpeg:
            log("Shrink previews is on but ffmpeg wasn't found; copying teasers as they are", "warning")
        return Settings(
            general.get("videoExtensions") or DEFAULT_VIDEO_EXTENSIONS,
            general.get("imageExtensions") or DEFAULT_IMAGE_EXTENSIONS,
            generated,
            (general.get("videoFileNamingAlgorithm") or "OSHASH").lower(),
            NamePatterns(plugin.get("coverNames") or DEFAULT_COVER_NAMES, DEFAULT_COVER_NAMES),
            NamePatterns(plugin.get("previewNames") or DEFAULT_PREVIEW_NAMES, DEFAULT_PREVIEW_NAMES),
            shrink_previews=shrink,
            preview_seconds=_as_seconds(plugin.get("previewSeconds", DEFAULT_PREVIEW_SECONDS)),
            preview_audio=bool(general.get("previewAudio")),
            preview_preset=(general.get("previewPreset") or "veryfast").lower(),
            ffmpeg=ffmpeg,
        )

    def plugin_settings(self):
        """This plugin's settings from Settings > Plugins ({} if unset)."""
        try:
            config = self.query("query($ids: [ID!]) { configuration { plugins(include: $ids) } }",
                                {"ids": [PLUGIN_ID]})["configuration"]["plugins"] or {}
        except (RuntimeError, KeyError, TypeError):
            return {}
        values = config.get(PLUGIN_ID) or {}
        # Unset settings come back missing, null or "": treat them all as unset.
        return {k: v for k, v in values.items() if v is not None and not (isinstance(v, str) and not v.strip())}

    _SCENE_FIELDS = "id files { path fingerprints { type value } }"

    def scene(self, scene_id):
        data = self.query(f"query($id: ID!) {{ findScene(id: $id) {{ {self._SCENE_FIELDS} }} }}", {"id": scene_id})
        return data.get("findScene")

    def all_scenes(self):
        data = self.query(f"{{ findScenes(filter: {{per_page: -1}}) {{ scenes {{ {self._SCENE_FIELDS} }} }} }}")
        return data["findScenes"]["scenes"]

    def set_cover(self, scene_id, path):
        self.query(
            "mutation($input: SceneUpdateInput!) { sceneUpdate(input: $input) { id } }",
            {"input": {"id": scene_id, "cover_image": image_data_uri(path)}},
        )


# --- Plugin entry points -------------------------------------------------

def apply_folder_media(stash, settings, scene):
    """Sets the scene's cover and preview from its folder. Returns a list of
    what was done, e.g. ["cover from /x/cover.jpg"]."""
    files = scene.get("files") or []
    if len(files) != 1:
        # Several files on one scene: no single folder the media clearly belongs to.
        return []
    video = files[0]
    if not os.path.isfile(video["path"]):
        return []  # inside a zip, or not reachable from here
    cover, preview = find_folder_media(video["path"], settings.video_extensions, settings.image_extensions,
                                       settings.cover_patterns, settings.preview_patterns)
    scene_id = str(scene["id"])
    done = []

    if cover:
        stash.set_cover(scene_id, cover)
        done.append(f"cover from {cover}")

    if preview:
        fingerprints = {f["type"]: f["value"] for f in video.get("fingerprints") or []}
        file_hash = fingerprints.get(settings.hash_algorithm)
        if not settings.generated_path:
            log(f"Scene {scene_id}: Stash has no generated folder set, can't add preview", "warning")
        elif not file_hash:
            log(f"Scene {scene_id}: no {settings.hash_algorithm} hash yet, can't add preview", "warning")
        else:
            destination = settings.preview_path(file_hash)
            shrunk = False
            if settings.shrink_previews and settings.ffmpeg:
                try:
                    shrink_into_place(settings, preview, destination)
                    shrunk = True
                except (RuntimeError, OSError) as e:
                    log(f"Scene {scene_id}: couldn't shrink {preview}, copying it instead: {e}", "warning")
            if not shrunk:
                if _extension(preview) not in PREFERRED_PREVIEW_EXTENSIONS:
                    log(f"Scene {scene_id}: {preview} may not play in browsers; mp4 (H.264) is safest", "warning")
                copy_into_place(preview, destination)
            # An animated .webp made from the old preview would otherwise stay,
            # and Stash's Generate (without overwrite) would never rebuild it.
            webp = settings.webp_path(file_hash)
            if os.path.exists(webp):
                os.remove(webp)
            done.append(f"preview from {preview}" + (f" (first {settings.preview_seconds:g}s, shrunk)" if shrunk else ""))

    for item in done:
        log(f"Scene {scene_id}: {item}")
    return done


def run_hook(stash, hook_context):
    if not hook_context.get("id"):
        return "No scene id in hook context"
    scene_id = str(hook_context["id"])  # Stash sends a number; GraphQL IDs are strings
    scene = stash.scene(scene_id)
    if not scene:
        return f"Scene {scene_id} not found"
    done = apply_folder_media(stash, stash.settings(), scene)
    return "; ".join(done) if done else "No folder media"


def run_task(stash):
    settings = stash.settings()
    scenes = stash.all_scenes()
    log(f"Checking {len(scenes)} scenes for folder media")
    covers = previews = 0
    for i, scene in enumerate(scenes):
        try:
            done = apply_folder_media(stash, settings, scene)
            covers += sum(1 for d in done if d.startswith("cover"))
            previews += sum(1 for d in done if d.startswith("preview"))
        except (OSError, RuntimeError, urllib.error.URLError) as e:
            log(f"Scene {scene.get('id')}: {e}", "warning")
        # Stash shows a "\x01p\x02<0..1>" line as the task's progress.
        print(f"\x01p\x02{(i + 1) / len(scenes)}", file=sys.stderr, flush=True)
    summary = f"Set {covers} covers and {previews} previews from folder media"
    log(summary)
    return summary


def main():
    plugin_input = json.loads(sys.stdin.read() or "{}")
    args = plugin_input.get("args") or {}
    stash = Stash(plugin_input.get("server_connection") or {})
    if args.get("hookContext"):
        output = run_hook(stash, args["hookContext"])
    else:
        output = run_task(stash)
    print(json.dumps({"output": output}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report to Stash rather than dying with a traceback
        log(f"{type(e).__name__}: {e}", "error")
        print(json.dumps({"error": str(e)}))
