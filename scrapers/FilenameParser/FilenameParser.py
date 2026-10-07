"""Stash scene scraper that derives metadata from a scene's file path.

Performers, studios and tags are matched against what already exists in
Stash, using the same rules as Stash's built-in Auto Tag task
(pkg/match/path.go):

- performers match on their primary name only (Auto Tag has alias matching
  for performers disabled), anywhere in the path
- studios match on name or alias; when several match, the one matching
  furthest right in the path wins
- tags match on name or alias, anywhere in the path
- anything flagged "Ignore auto tag" in Stash is skipped

On top of that it extracts a date (or at least a year) from the filename and
builds a title from what is left once dates, resolution/codec/source noise,
matched performer/studio names and empty brackets are removed.

Series-dated filenames ("[Series] YYYY.MM.DD Title - Performer1, Performer2
[WEBDL-1080p, h264].mp4", or the older "Performers - Title" order) are parsed
exactly: series becomes the studio and the performer list is taken from the
filename, including performers that don't exist in Stash yet.

Standard library only, so it runs on whatever Python Stash is configured with.
"""

import configparser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "url": "http://localhost:9999",
    "api_key": "",
    "review_tag": "Date Needs Review",
    "two_digit_year_order": "auto",
    "match_scope": "path",
    "cache_ttl_seconds": "300",
}


def log(message):
    # Stash shows a script scraper's stderr in its log; stdout is the result.
    print(f"[FilenameParser] {message}", file=sys.stderr)


# --- Auto Tag-compatible name matching -----------------------------------

# Stash's nameToRegexp: a space in a name matches any run of ".-_ " in the
# path (including none), and the name must be bounded by start/end, "_" or a
# non-letter/digit character. Lookarounds are used instead of consuming the
# boundary so match spans cover just the name (needed for title stripping).
_SEPARATOR = r"[.\-_ ]*"
_BOUNDARY_BEFORE = r"(?<![^\W_])"
_BOUNDARY_AFTER = r"(?![^\W_])"


def name_to_regex(name):
    parts = [re.escape(p) for p in name.strip().split(" ") if p]
    return re.compile(_BOUNDARY_BEFORE + _SEPARATOR.join(parts) + _BOUNDARY_AFTER, re.IGNORECASE)


def find_name(name, text):
    """Returns all (start, end) spans where name matches text."""
    if not name or not name.strip():
        return []
    return [m.span() for m in name_to_regex(name).finditer(text)]


@dataclass
class Entity:
    name: str
    aliases: list = field(default_factory=list)


@dataclass
class Library:
    performers: list
    studios: list
    tags: list


def match_performers(library, text):
    """Performers whose primary name appears in text, with their spans."""
    matched = []
    for p in library.performers:
        spans = find_name(p.name, text)
        if spans:
            matched.append((p, spans))
    # Order by first appearance, so "Alice & Bob" lists Alice first.
    return sorted(matched, key=lambda m: m[1][0][0])


def match_studio(library, text):
    """The studio whose name or alias matches furthest right in text."""
    best, best_index = None, -1
    for s in library.studios:
        for candidate in [s.name] + s.aliases:
            spans = find_name(candidate, text)
            if spans and spans[-1][0] > best_index:
                best, best_index = s, spans[-1][0]
    return best


def match_tags(library, text):
    return [t for t in library.tags if any(find_name(n, text) for n in [t.name] + t.aliases)]


def lookup_entity(entities, name, use_aliases=True):
    wanted = name.strip().casefold()
    for e in entities:
        names = [e.name] + (e.aliases if use_aliases else [])
        if any(n.casefold() == wanted for n in names):
            return e
    return None


# --- Noise (resolution, codec, source, ...) ------------------------------

_NOISE_TOKENS = [
    r"\d{3,4}[pi]", r"\d{3,4}x\d{3,4}", r"[2458]k", r"uhd", r"fhd", r"qhd", r"hd", r"sd",
    r"hdr(?:10)?\+?", r"dolby[ .]?vision", r"sdr",
    r"[xh][ .]?26[45]", r"hevc", r"avc", r"av1", r"vp9", r"xvid", r"divx", r"mpeg-?[24]",
    r"(?:8|10|12)[ .-]?bit",
    r"aac(?:[ .]?2\.0)?", r"e-?ac-?3", r"ac3", r"dts(?:-?hd)?", r"ddp?[ .]?[257]\.[01]", r"flac", r"opus", r"mp3",
    r"\d{2,3}[ .]?fps",
    r"web[ .-]?dl", r"web[ .-]?rip", r"webrip", r"hdtv", r"blu-?ray", r"bdrip", r"brrip", r"dvdrip", r"remux",
    r"mp4", r"mkv", r"wmv", r"m4v",
    r"xxx", r"repack", r"proper", r"internal",
]
_NOISE_RE = re.compile(
    _BOUNDARY_BEFORE + r"(?:" + "|".join(_NOISE_TOKENS) + r")" + _BOUNDARY_AFTER, re.IGNORECASE
)
# A download tool's quality suffix, e.g. " [WEBDL-1080p, h264]"
_QUALITY_SUFFIX_RE = re.compile(r"\s*\[[A-Z]+-\d+p, [^\]]+\]\s*$")
# Scene-release group after a noise token, e.g. the "-WRB" in "XXX.1080p.MP4-WRB"
_RELEASE_GROUP_RE = re.compile(
    r"(?<=[A-Za-z0-9])-[A-Za-z0-9]+$"
)
# CRC/hash tags like [1A2B3C4D]
_HASH_RE = re.compile(r"[\[(][0-9A-Fa-f]{8}[\])]")


def find_noise_spans(text):
    spans = [m.span() for m in _NOISE_RE.finditer(text)]
    spans += [m.span() for m in _QUALITY_SUFFIX_RE.finditer(text)]
    spans += [m.span() for m in _HASH_RE.finditer(text)]
    group = _RELEASE_GROUP_RE.search(text)
    # Only treat a trailing "-GROUP" as a release group if it directly follows
    # a noise token, so a title ending in a hyphenated word survives.
    if group and any(end == group.start() for _, end in spans):
        spans.append(group.span())
    return spans


# --- Dates ---------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAME = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_D = r"(?<!\d)"
_ND = r"(?!\d)"
_SEP = r"[-._ ]"
_YEAR = r"(?P<y>(?:19|20)\d{2})"


@dataclass
class DateCandidate:
    span: tuple
    value: str = None        # chosen date: "YYYY-MM-DD", "YYYY-MM" or "YYYY"
    precision: int = 0       # 3 = day, 2 = month, 1 = year
    ambiguous: bool = False


def _valid(y, m, d):
    try:
        date(y, m, d)
    except ValueError:
        return False
    return 1950 <= y <= date.today().year + 1


def _century(yy):
    return 2000 + yy if yy <= (date.today().year % 100) + 1 else 1900 + yy


def _resolve(interpretations, span):
    """Turns the valid (y, m, d) readings of a date into a candidate.

    One distinct reading is unambiguous. Several readings are ambiguous: the
    candidate keeps just the year if they all agree on it, otherwise nothing,
    and is flagged so the caller can add the review tag.
    """
    valid = sorted({i for i in interpretations if _valid(*i)})
    if not valid:
        return None
    if len(valid) == 1:
        y, m, d = valid[0]
        return DateCandidate(span, f"{y:04d}-{m:02d}-{d:02d}", 3)
    years = {y for y, _, _ in valid}
    value = f"{years.pop():04d}" if len(years) == 1 else None
    return DateCandidate(span, value, 1 if value else 0, ambiguous=True)


def find_dates(text, two_digit_year_order="auto"):
    """All date-like spans in text, as DateCandidates (unparseable ones skipped)."""
    candidates = []
    flags = re.IGNORECASE

    for m in re.finditer(_D + _YEAR + r"(?P<s>" + _SEP + r")(?P<m>\d{1,2})(?P=s)(?P<d>\d{1,2})" + _ND, text):
        c = _resolve([(int(m["y"]), int(m["m"]), int(m["d"]))], m.span())
        if c:
            candidates.append(c)

    for m in re.finditer(_D + r"(?P<a>\d{1,2})(?P<s>" + _SEP + r")(?P<b>\d{1,2})(?P=s)" + _YEAR + _ND, text):
        y, a, b = int(m["y"]), int(m["a"]), int(m["b"])
        c = _resolve([(y, b, a), (y, a, b)], m.span())  # day-first, month-first
        if c:
            candidates.append(c)

    for m in re.finditer(_D + r"(?P<y>(?:19|20)\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])" + _ND, text):
        c = _resolve([(int(m["y"]), int(m["m"]), int(m["d"]))], m.span())
        if c:
            candidates.append(c)

    # Two-digit years, e.g. the scene-release style "Site.23.04.15.Title".
    # Space-separated is left alone: too easily part of a title.
    for m in re.finditer(_D + r"(?P<a>\d{2})(?P<s>[-._])(?P<b>\d{2})(?P=s)(?P<c>\d{2})" + _ND, text):
        a, b, cc = int(m["a"]), int(m["b"]), int(m["c"])
        ymd = (_century(a), b, cc)
        readings = [ymd] if two_digit_year_order == "ymd" else [ymd, (_century(cc), b, a), (_century(cc), a, b)]
        c = _resolve(readings, m.span())
        if c:
            candidates.append(c)

    day = r"(?P<d>\d{1,2})(?:st|nd|rd|th)?"
    for pattern in (
        _D + day + _SEP + r"*(?:of" + _SEP + r"+)?" + _MONTH_NAME + r"\b" + _SEP + r"*,?" + _SEP + r"*" + _YEAR + _ND,
        r"\b" + _MONTH_NAME + r"\b" + _SEP + r"*" + day + r",?" + _SEP + r"*" + _YEAR + _ND,
    ):
        for m in re.finditer(pattern, text, flags):
            c = _resolve([(int(m["y"]), _MONTHS[m["mon"][:3].lower()], int(m["d"]))], m.span())
            if c:
                candidates.append(c)

    for m in re.finditer(r"\b" + _MONTH_NAME + r"\b" + _SEP + r"*,?" + _SEP + r"*" + _YEAR + _ND, text, flags):
        y = int(m["y"])
        if _valid(y, 1, 1):
            candidates.append(DateCandidate(m.span(), f"{y:04d}-{_MONTHS[m['mon'][:3].lower()]:02d}", 2))

    # Year-month, only with "-" or "." so "2023 10 Things" isn't read as October.
    for m in re.finditer(_D + _YEAR + r"[-.](?P<m>0[1-9]|1[0-2])" + _ND + r"(?![-.]\d)", text):
        y = int(m["y"])
        if _valid(y, 1, 1):
            candidates.append(DateCandidate(m.span(), f"{y:04d}-{int(m['m']):02d}", 2))

    for m in re.finditer(r"[(\[]?" + _D + _YEAR + _ND + r"[)\]]?", text):
        y = int(m["y"])
        if _valid(y, 1, 1):
            candidates.append(DateCandidate(m.span(), f"{y:04d}", 1))

    # Drop candidates contained in a longer one (the year inside a full date).
    candidates.sort(key=lambda c: (c.span[0], -(c.span[1] - c.span[0])))
    kept = []
    for c in candidates:
        if not any(k.span[0] <= c.span[0] and c.span[1] <= k.span[1] for k in kept):
            kept.append(c)
    return kept


def choose_date(candidates):
    """Picks the best candidate: the most precise unambiguous one, first in the
    name on a tie. An ambiguous date only wins if nothing unambiguous is at
    least as precise. Returns (value or None, needs_review)."""
    if not candidates:
        return None, False
    best = sorted(candidates, key=lambda c: (-c.precision, c.ambiguous, c.span[0]))[0]
    unambiguous_day = any(c.precision == 3 and not c.ambiguous for c in candidates)
    needs_review = any(c.ambiguous for c in candidates) and not unambiguous_day
    return best.value, needs_review


# --- Title cleanup -------------------------------------------------------

_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_EMPTY_BRACKETS_RE = re.compile(r"[(\[{][\s\-–—,.&+_:;|]*[)\]}]")
_JOINER_WORDS = r"(?:and|with|feat\.?|ft\.?|featuring|vs\.?)"
_EDGE_RE = re.compile(
    r"^(?:[\s\-–—,.&+_:;|]|" + _JOINER_WORDS + r"(?=[\s\-–—,.&+_:;|]|$))+"
    r"|(?:(?<=[\s\-–—,&+_:;|])" + _JOINER_WORDS + r"|[\s\-–—,.&+_:;|])+$",
    re.IGNORECASE,
)
_SEPARATOR_RUN_RE = re.compile(r"(?:\s*[\-–—,&+|:;]\s*){2,}")


def remove_spans(text, spans):
    spans = sorted(spans)
    out, pos = [], 0
    for start, end in spans:
        if start > pos:
            out.append(text[pos:start])
        pos = max(pos, end)
    out.append(text[pos:])
    # Join with a space so removing a token never glues its neighbours together.
    return " ".join(out)


def _remove_unbalanced_brackets(text):
    stack, unmatched = [], set()
    closers = {v: k for k, v in _BRACKETS.items()}
    for i, ch in enumerate(text):
        if ch in _BRACKETS:
            stack.append(i)
        elif ch in closers:
            if stack and text[stack[-1]] == closers[ch]:
                stack.pop()
            else:
                unmatched.add(i)
    unmatched.update(stack)
    return "".join(ch for i, ch in enumerate(text) if i not in unmatched)


def _collapse_separator_run(m):
    run = m.group(0)
    if any(ch in run for ch in "-–—"):
        return " - "
    stripped = run.strip()
    return f"{stripped[0]} " if stripped else " "


def clean_title(text, dotted):
    if dotted:
        text = text.replace(".", " ")
    text = text.replace("_", " ")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\s+", " ", text)
        text = _EMPTY_BRACKETS_RE.sub(" ", text)
        text = _remove_unbalanced_brackets(text)
        text = _SEPARATOR_RUN_RE.sub(_collapse_separator_run, text)
        text = re.sub(r"\s+([,)\]}])", r"\1", text)
        text = re.sub(r"([(\[{])\s+", r"\1", text)
        text = _EDGE_RE.sub("", text)
        text = text.strip()
    return text


def is_dotted(basename):
    """Whether dots/underscores are being used as word separators."""
    return " " not in basename and (basename.count(".") + basename.count("_")) >= 2


# --- Series-dated filename format ------------------------------------------------

_SERIES_NAME_RE = re.compile(
    r"^\[(?P<series>[^\]]+)\] (?P<y>\d{4})\.(?P<m>\d{2})\.(?P<d>\d{2}) (?P<rest>.+?)"
    r"(?: \[(?P<format>[A-Z]+)-(?P<height>\d+)p, (?P<codec>[^\]]+)\])?$"
)
_TRUNCATED_RE = re.compile(r"\s*\+\d+$")


@dataclass
class SeriesName:
    series: str
    date: str
    title: str
    performers: list


def _split_performers(text):
    names = [n.strip() for n in _TRUNCATED_RE.sub("", text).split(",")]
    return [n for n in names if n]


def parse_series_name(basename, library):
    m = _SERIES_NAME_RE.match(basename)
    if not m or not _valid(int(m["y"]), int(m["m"]), int(m["d"])):
        return None
    rest = m["rest"]
    title, performers = rest, []
    if " - " in rest:
        # Current order: "Title - Performers"; titles may contain " - " too, so
        # the performer list is whatever follows the last one.
        cur_title, cur_tail = rest.rsplit(" - ", 1)
        # Older order: "Performers - Title".
        old_head, old_title = rest.split(" - ", 1)

        def known(names):
            return sum(1 for n in names if lookup_entity(library.performers, n))

        cur_names, old_names = _split_performers(cur_tail), _split_performers(old_head)
        if known(old_names) > known(cur_names):
            title, performers = old_title, old_names
        else:
            title, performers = cur_title, cur_names
    elif _TRUNCATED_RE.fullmatch(" " + rest.split(" ")[-1]):
        title = _TRUNCATED_RE.sub("", rest)
    return SeriesName(m["series"], f"{m['y']}-{m['m']}-{m['d']}", title.strip(), performers)


# --- Scrape --------------------------------------------------------------

def _unique(names):
    seen, out = set(), []
    for n in names:
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return out


def scrape_path(path, library, options=None):
    options = {**DEFAULT_CONFIG, **(options or {})}
    path = path.replace("\\", "/")
    basename = os.path.basename(path)
    stem, ext = os.path.splitext(basename)
    if len(ext) > 5 or " " in ext:  # not really an extension
        stem = basename
    full = path[: len(path) - len(basename)] + stem
    match_text = full if options["match_scope"] == "path" else stem
    offset = len(match_text) - len(stem)  # where the basename starts within match_text

    performers = match_performers(library, match_text)
    studio = match_studio(library, match_text)
    tags = [t.name for t in match_tags(library, match_text)]
    result = {}

    series_name = parse_series_name(stem, library)
    if series_name:
        result["title"] = series_name.title
        result["date"] = series_name.date
        known = lookup_entity(library.studios, series_name.series)
        result["studio"] = known.name if known else series_name.series
        names = []
        for n in series_name.performers:
            known = lookup_entity(library.performers, n)
            names.append(known.name if known else n)
        result["performers"] = _unique(names + [p.name for p, _ in performers])
        result["tags"] = _unique(tags)
        return _to_output(result)

    noise = find_noise_spans(stem)
    dates = [
        d for d in find_dates(stem, options["two_digit_year_order"])
        if not any(s <= d.span[0] < e or s < d.span[1] <= e for s, e in noise)
    ]
    date_value, needs_review = choose_date(dates)

    name_spans = []
    for _, spans in performers:
        name_spans += [(s - offset, e - offset) for s, e in spans if s >= offset]
    if studio:
        for candidate in [studio.name] + studio.aliases:
            name_spans += find_name(candidate, stem)

    base_spans = noise + [d.span for d in dates]
    dotted = is_dotted(stem)
    title = clean_title(remove_spans(stem, base_spans + name_spans), dotted)
    if not title:
        # Everything was a name; better a title with names in it than none.
        title = clean_title(remove_spans(stem, base_spans), dotted)

    if title:
        result["title"] = title
    if date_value:
        result["date"] = date_value
    if studio:
        result["studio"] = studio.name
    result["performers"] = _unique(p.name for p, _ in performers)
    if needs_review and options["review_tag"]:
        tags.append(options["review_tag"])
    result["tags"] = _unique(tags)
    return _to_output(result)


def _to_output(result):
    out = {}
    for key in ("title", "date"):
        if result.get(key):
            out[key] = result[key]
    if result.get("studio"):
        out["studio"] = {"name": result["studio"]}
    for key in ("performers", "tags"):
        if result.get(key):
            out[key] = [{"name": n} for n in result[key]]
    return out


# --- Stash GraphQL -------------------------------------------------------

class Stash:
    def __init__(self, url, api_key=""):
        self.endpoint = url.rstrip("/") + "/graphql"
        self.api_key = api_key

    def query(self, query, variables=None):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        request = urllib.request.Request(self.endpoint, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL error: {payload['errors']}")
        return payload["data"]

    def scene_path(self, scene_id):
        data = self.query("query($id: ID!) { findScene(id: $id) { files { path } } }", {"id": scene_id})
        files = (data.get("findScene") or {}).get("files") or []
        return files[0]["path"] if files else None

    def library(self):
        data = self.query(
            """{
              findPerformers(filter: {per_page: -1}) { performers { name alias_list ignore_auto_tag } }
              findStudios(filter: {per_page: -1}) { studios { name aliases ignore_auto_tag } }
              findTags(filter: {per_page: -1}) { tags { name aliases ignore_auto_tag } }
            }"""
        )
        return {
            "performers": [
                {"name": p["name"], "aliases": p["alias_list"]}
                for p in data["findPerformers"]["performers"] if not p["ignore_auto_tag"]
            ],
            "studios": [
                {"name": s["name"], "aliases": s["aliases"]}
                for s in data["findStudios"]["studios"] if not s["ignore_auto_tag"]
            ],
            "tags": [
                {"name": t["name"], "aliases": t["aliases"]}
                for t in data["findTags"]["tags"] if not t["ignore_auto_tag"]
            ],
        }


def load_library(stash, ttl_seconds):
    """Fetches performers/studios/tags, cached on disk for ttl_seconds so a
    batch Identify run doesn't re-download the whole library per scene."""
    cache_path = os.path.join(SCRIPT_DIR, ".library-cache.json")
    raw = None
    if ttl_seconds > 0:
        try:
            if time.time() - os.path.getmtime(cache_path) < ttl_seconds:
                with open(cache_path, encoding="utf-8") as f:
                    raw = json.load(f)
        except (OSError, ValueError):
            raw = None
    if raw is None:
        raw = stash.library()
        if ttl_seconds > 0:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(raw, f)
            except OSError as e:
                log(f"Could not write library cache: {e}")
    return Library(
        performers=[Entity(**p) for p in raw["performers"]],
        studios=[Entity(**s) for s in raw["studios"]],
        tags=[Entity(**t) for t in raw["tags"]],
    )


def load_config():
    parser = configparser.ConfigParser()
    parser.read(os.path.join(SCRIPT_DIR, "config.ini"), encoding="utf-8")
    config = dict(DEFAULT_CONFIG)
    for section in ("stash", "parser"):
        if parser.has_section(section):
            config.update({k: v.strip() for k, v in parser.items(section)})
    config["url"] = os.environ.get("STASH_URL", config["url"])
    config["api_key"] = os.environ.get("STASH_API_KEY", config["api_key"])
    return config


def main():
    fragment = json.loads(sys.stdin.read() or "{}")
    config = load_config()
    stash = Stash(config["url"], config["api_key"])

    files = fragment.get("files") or []
    path = files[0].get("path") if files else None
    if not path and fragment.get("id"):
        # Older Stash versions don't include files in the scraper input.
        path = stash.scene_path(fragment["id"])
    if not path:
        log("Scene has no file path to parse")
        print(json.dumps({}))
        return

    library = load_library(stash, int(config["cache_ttl_seconds"] or 0))
    result = scrape_path(path, library, config)
    log(f"{os.path.basename(path)} -> {json.dumps(result, ensure_ascii=False)}")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError) as e:
        log(f"Could not query Stash: {e}. Check url/api_key in config.ini.")
        print(json.dumps({}))
