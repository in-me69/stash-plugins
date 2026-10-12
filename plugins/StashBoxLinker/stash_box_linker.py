"""Stash plugin: when a studio or performer gets an ID from one stash-box
(e.g. StashDB), find the same studio/performer on every other configured
stash-box (e.g. ThePornDB) and add those IDs too.

Only the stash-box IDs are added; nothing else about the studio/performer is
changed. When the plugin can't be sure it found the same one, it links
nothing and adds a review tag instead, so those can be linked by hand.

The searching itself is done by Stash (scrapeSingleStudio/Performer against
each stash-box), using the API keys already set up in Stash.

Standard library only, so it runs on whatever Python Stash is configured with.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

PLUGIN_ID = "StashBoxLinker"
DEFAULT_REVIEW_TAG = "Stash-box Match Needs Review"
MAX_ALIAS_SEARCHES = 3
# Pause between stash-box searches in the bulk tasks, to go easy on them.
TASK_SEARCH_DELAY_SECONDS = 0.5


def log(message, level="info"):
    """Writes to Stash's log; the "\\x01<level>\\x02" prefix sets the level
    (trace, debug, info, warning, error)."""
    prefix = f"\x01{level[0]}\x02"
    for line in str(message).splitlines() or [""]:
        print(f"{prefix}[StashBoxLinker] {line}", file=sys.stderr, flush=True)


# --- Comparing entities --------------------------------------------------
#
# Local entities and stash-box candidates are both plain dicts:
#   name, aliases (list), urls (list), and for studios "parent" (name or
#   None), for performers birthdate, gender, country, disambiguation.

def squash(text):
    """Name for comparison: lower case, letters and digits only."""
    return re.sub(r"[\W_]+", "", text or "").casefold()


def split_aliases(aliases):
    """Stash-box candidates give aliases as one comma-separated string."""
    if isinstance(aliases, str):
        aliases = aliases.split(",")
    return [a.strip() for a in aliases or [] if a and a.strip()]


def all_names(entity):
    return {squash(n) for n in [entity.get("name")] + list(entity.get("aliases") or []) if squash(n)}


def names_match(local, candidate):
    return bool(all_names(local) & all_names(candidate))


def normalize_url(url):
    """Comparable form of a URL: no scheme, "www.", query or trailing slash,
    lower case; x.com counts as twitter.com."""
    url = (url or "").strip().lower()
    url = re.sub(r"^[a-z]+://", "", url)
    url = re.sub(r"^(www\.|m\.|mobile\.)", "", url)
    url = re.sub(r"[?#].*$", "", url).rstrip("/")
    return re.sub(r"^x\.com/", "twitter.com/", url)


def urls_overlap(local, candidate):
    a = {normalize_url(u) for u in local.get("urls") or [] if normalize_url(u)}
    b = {normalize_url(u) for u in candidate.get("urls") or [] if normalize_url(u)}
    return bool(a & b)


def _norm_gender(value):
    return re.sub(r"[\s-]+", "_", (value or "").strip()).upper()


def dates_agree(a, b):
    """Dates agree if one is a prefix of the other ("1990" vs "1990-05-01")."""
    if not a or not b:
        return None  # nothing to compare
    return a.startswith(b) or b.startswith(a)


def performer_contradictions(local, candidate):
    """Details both sides have that disagree, e.g. ["birthdate"]."""
    found = []
    if dates_agree(local.get("birthdate"), candidate.get("birthdate")) is False:
        found.append("birthdate")
    lg, cg = _norm_gender(local.get("gender")), _norm_gender(candidate.get("gender"))
    if lg and cg and lg != cg:
        found.append("gender")
    lc, cc = (local.get("country") or "").strip().upper(), (candidate.get("country") or "").strip().upper()
    # Only compare country codes; names ("United States") vs codes ("US") can't be compared reliably.
    if len(lc) == 2 and len(cc) == 2 and lc != cc:
        found.append("country")
    ld, cd = squash(local.get("disambiguation")), squash(candidate.get("disambiguation"))
    if ld and cd and ld != cd:
        found.append("disambiguation")
    return found


def performer_corroborated(local, candidate):
    """A detail beyond the name that confirms it's the same performer."""
    full_dates = len(local.get("birthdate") or "") == 10 and len(candidate.get("birthdate") or "") == 10
    return (full_dates and local["birthdate"] == candidate["birthdate"]) or urls_overlap(local, candidate)


def studio_contradictions(local, candidate):
    lp, cp = squash(local.get("parent")), squash(candidate.get("parent"))
    return ["parent studio"] if lp and cp and lp != cp else []


class Decision:
    """Outcome of matching one entity against one stash-box: "link" (with
    the candidate), "uncertain" (worth a human look) or "none" (not there)."""

    def __init__(self, outcome, candidate=None, reason=""):
        self.outcome, self.candidate, self.reason = outcome, candidate, reason

    def __repr__(self):
        return f"Decision({self.outcome!r}, {self.reason!r})"


def _describe(candidates):
    return ", ".join(f"{c.get('name')} ({c.get('remote_site_id')})" for c in candidates)


def decide_studio(local, candidates):
    named = [c for c in candidates if names_match(local, c)]
    if not named:
        return Decision("none", reason="no studio with that name")
    fitting = [c for c in named if not studio_contradictions(local, c)]
    if not fitting:
        return Decision("uncertain", reason=f"name matches but parent studio differs: {_describe(named)}")
    if len(fitting) == 1:
        return Decision("link", fitting[0], "name match")
    by_url = [c for c in fitting if urls_overlap(local, c)]
    if len(by_url) == 1:
        return Decision("link", by_url[0], "name and website match")
    return Decision("uncertain", reason=f"several studios with that name: {_describe(fitting)}")


def decide_performer(local, candidates):
    named = [c for c in candidates if names_match(local, c)]
    if not named:
        return Decision("none", reason="no performer with that name")
    fitting = [c for c in named if not performer_contradictions(local, c)]
    if not fitting:
        details = sorted({d for c in named for d in performer_contradictions(local, c)})
        return Decision("uncertain", reason=f"name matches but {', '.join(details)} differ: {_describe(named)}")
    corroborated = [c for c in fitting if performer_corroborated(local, c)]
    if len(corroborated) == 1:
        return Decision("link", corroborated[0], "name and birthdate/links match")
    if len(corroborated) > 1:
        return Decision("uncertain", reason=f"several matching performers: {_describe(corroborated)}")
    # Nothing beyond the name to go on. A single full name ("Venus
    # Afrodita") is distinctive enough; a single word ("Lily") isn't.
    if len(fitting) == 1 and len((local.get("name") or "").split()) >= 2:
        return Decision("link", fitting[0], "only performer with that full name")
    if len(fitting) == 1:
        return Decision("uncertain", reason=f"single-word name with nothing else to confirm it: {_describe(fitting)}")
    return Decision("uncertain", reason=f"several performers with that name: {_describe(fitting)}")


# --- Stash GraphQL -------------------------------------------------------

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
        # Stash-box searches go through Stash and can be slow.
        with urllib.request.urlopen(request, timeout=120, context=context) as response:
            payload = json.load(response)
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL error: {payload['errors']}")
        return payload["data"]

    def stash_boxes(self):
        """[(endpoint, name)] of the stash-boxes configured in Stash."""
        boxes = self.query("{ configuration { general { stashBoxes { endpoint name } } } }")
        return [(b["endpoint"], b.get("name") or b["endpoint"])
                for b in boxes["configuration"]["general"]["stashBoxes"]]

    def review_tag_name(self):
        try:
            config = self.query("query($ids: [ID!]) { configuration { plugins(include: $ids) } }",
                                {"ids": [PLUGIN_ID]})["configuration"]["plugins"] or {}
        except RuntimeError:
            return DEFAULT_REVIEW_TAG
        name = (config.get(PLUGIN_ID) or {}).get("reviewTag")
        return name.strip() if isinstance(name, str) and name.strip() else DEFAULT_REVIEW_TAG


class Kind:
    """What differs between studios and performers when talking to Stash."""

    def __init__(self, name, fields, local, scraped_fields, candidate, decide):
        self.name = name  # "studio" / "performer"
        self.fields = fields  # GraphQL fields to load locally
        self.local = local  # GraphQL result -> local dict
        self.scraped_fields = scraped_fields
        self.candidate = candidate  # scraped result -> candidate dict
        self.decide = decide

    @property
    def title(self):
        return self.name.capitalize()

    def find(self, stash, entity_id):
        data = stash.query(f"query($id: ID!) {{ find{self.title}(id: $id) {{ {self.fields} }} }}", {"id": entity_id})
        return data.get(f"find{self.title}")

    def find_all_with_stash_ids(self, stash):
        data = stash.query(
            f"{{ find{self.title}s(filter: {{per_page: -1}}, "
            f"{self.name}_filter: {{stash_id_endpoint: {{modifier: NOT_NULL}}}}) "
            f"{{ {self.name}s {{ {self.fields} }} }} }}"
        )
        return data[f"find{self.title}s"][f"{self.name}s"]

    def ids_linked_to(self, stash, endpoint, stash_id):
        data = stash.query(
            f"query($f: {self.title}FilterType) {{ find{self.title}s({self.name}_filter: $f) {{ {self.name}s {{ id }} }} }}",
            {"f": {"stash_id_endpoint": {"endpoint": endpoint, "stash_id": stash_id, "modifier": "EQUALS"}}},
        )
        return [e["id"] for e in data[f"find{self.title}s"][f"{self.name}s"]]

    def search(self, stash, endpoint, query):
        data = stash.query(
            f"query($s: ScraperSourceInput!, $i: ScrapeSingle{self.title}Input!) "
            f"{{ scrapeSingle{self.title}(source: $s, input: $i) {{ {self.scraped_fields} }} }}",
            {"s": {"stash_box_endpoint": endpoint}, "i": {"query": query}},
        )
        return [self.candidate(c) for c in data[f"scrapeSingle{self.title}"] if c.get("remote_site_id")]

    def update(self, stash, entity_id, changes):
        stash.query(
            f"mutation($i: {self.title}UpdateInput!) {{ {self.name}Update(input: $i) {{ id }} }}",
            {"i": {"id": entity_id, **changes}},
        )


STUDIO = Kind(
    "studio",
    "id name aliases urls parent_studio { name } stash_ids { endpoint stash_id } tags { id name }",
    lambda s: {"name": s["name"], "aliases": s.get("aliases") or [], "urls": s.get("urls") or [],
               "parent": (s.get("parent_studio") or {}).get("name")},
    "name aliases urls parent { name } remote_site_id",
    lambda c: {"name": c.get("name"), "aliases": split_aliases(c.get("aliases")), "urls": c.get("urls") or [],
               "parent": (c.get("parent") or {}).get("name"), "remote_site_id": c["remote_site_id"]},
    decide_studio,
)

PERFORMER = Kind(
    "performer",
    "id name disambiguation alias_list urls birthdate gender country stash_ids { endpoint stash_id } tags { id name }",
    lambda p: {"name": p["name"], "aliases": p.get("alias_list") or [], "urls": p.get("urls") or [],
               "birthdate": p.get("birthdate"), "gender": p.get("gender"), "country": p.get("country"),
               "disambiguation": p.get("disambiguation")},
    "name disambiguation aliases urls birthdate gender country remote_site_id",
    lambda c: {"name": c.get("name"), "aliases": split_aliases(c.get("aliases")), "urls": c.get("urls") or [],
               "birthdate": c.get("birthdate"), "gender": c.get("gender"), "country": c.get("country"),
               "disambiguation": c.get("disambiguation"), "remote_site_id": c["remote_site_id"]},
    decide_performer,
)

KINDS = {"studio": STUDIO, "performer": PERFORMER}


# --- Linking -------------------------------------------------------------

def find_candidates(kind, stash, endpoint, local, delay=0.0):
    """Searches a stash-box by name, then by up to MAX_ALIAS_SEARCHES aliases
    if the name finds no name match."""
    seen, candidates = set(), []
    for query in [local["name"]] + local["aliases"][:MAX_ALIAS_SEARCHES]:
        if delay:
            time.sleep(delay)
        for c in kind.search(stash, endpoint, query):
            if c["remote_site_id"] not in seen:
                seen.add(c["remote_site_id"])
                candidates.append(c)
        if any(names_match(local, c) for c in candidates):
            break
    return candidates


def link_entity(kind, stash, entity, boxes, review_tag, delay=0.0):
    """Looks for `entity` on every configured stash-box it has no ID for yet,
    adds the IDs it's sure of, and adds/removes the review tag. Returns
    (number of IDs added, whether anything was uncertain)."""
    linked = {s["endpoint"] for s in entity.get("stash_ids") or []}
    if not linked:
        return 0, False  # only entities already matched to a stash-box
    local = kind.local(entity)
    label = f"{kind.title} {entity['name']!r} ({entity['id']})"
    new_ids, uncertain = [], False

    for endpoint, box_name in boxes:
        if endpoint in linked:
            continue
        try:
            candidates = find_candidates(kind, stash, endpoint, local, delay)
        except RuntimeError as e:
            log(f"{label}: searching {box_name} failed: {e}", "warning")
            continue
        decision = kind.decide(local, candidates)
        if decision.outcome == "link":
            remote_id = decision.candidate["remote_site_id"]
            others = [i for i in kind.ids_linked_to(stash, endpoint, remote_id) if str(i) != str(entity["id"])]
            if others:
                decision = Decision("uncertain", reason=f"{box_name} ID {remote_id} is already linked to {kind.name} {others[0]}")
        if decision.outcome == "link":
            new_ids.append({"endpoint": endpoint, "stash_id": decision.candidate["remote_site_id"]})
            log(f"{label}: linked to {box_name} {decision.candidate['name']!r} "
                f"({decision.candidate['remote_site_id']}): {decision.reason}")
        elif decision.outcome == "uncertain":
            uncertain = True
            log(f"{label}: not linked to {box_name}, needs review: {decision.reason}", "warning")
        else:
            log(f"{label}: not found on {box_name} ({decision.reason})", "debug")

    changes = {}
    if new_ids:
        changes["stash_ids"] = [{"endpoint": s["endpoint"], "stash_id": s["stash_id"]}
                                for s in entity["stash_ids"]] + new_ids
    tag_ids = [t["id"] for t in entity.get("tags") or []]
    has_tag = any(t["name"].casefold() == review_tag.casefold() for t in entity.get("tags") or [])
    if uncertain and not has_tag:
        changes["tag_ids"] = tag_ids + [review_tag_id(stash, review_tag)]
    elif not uncertain and has_tag:
        changes["tag_ids"] = [t["id"] for t in entity["tags"] if t["name"].casefold() != review_tag.casefold()]
    if changes:
        kind.update(stash, entity["id"], changes)
    return len(new_ids), uncertain


_tag_ids = {}


def review_tag_id(stash, name):
    """The review tag's ID, creating the tag if it doesn't exist."""
    if name not in _tag_ids:
        data = stash.query(
            "query($f: TagFilterType) { findTags(tag_filter: $f) { tags { id name } } }",
            {"f": {"name": {"value": name, "modifier": "EQUALS"}}},
        )
        tags = [t for t in data["findTags"]["tags"] if t["name"].casefold() == name.casefold()]
        if tags:
            _tag_ids[name] = tags[0]["id"]
        else:
            created = stash.query("mutation($i: TagCreateInput!) { tagCreate(input: $i) { id } }",
                                  {"i": {"name": name}})
            _tag_ids[name] = created["tagCreate"]["id"]
            log(f"Created tag {name!r}")
    return _tag_ids[name]


# --- Plugin entry points -------------------------------------------------

def run_hook(stash, hook_context):
    hook_type = hook_context.get("type") or ""
    kind = KINDS.get(hook_type.split(".")[0].lower())
    if not kind or not hook_context.get("id"):
        return f"Nothing to do for {hook_type}"
    if hook_type.endswith("Update.Post") and "stash_ids" not in (hook_context.get("inputFields") or []):
        # Only react to updates that change the stash-box IDs, not every edit.
        return "Stash-box IDs unchanged"
    entity = kind.find(stash, str(hook_context["id"]))
    if not entity:
        return f"{kind.title} {hook_context['id']} not found"
    added, uncertain = link_entity(kind, stash, entity, stash.stash_boxes(), stash.review_tag_name())
    return f"Added {added} stash-box IDs" + ("; needs review" if uncertain else "")


def run_task(stash, kind_name):
    kind = KINDS[kind_name]
    boxes = stash.stash_boxes()
    review_tag = stash.review_tag_name()
    configured = {endpoint for endpoint, _ in boxes}
    entities = [e for e in kind.find_all_with_stash_ids(stash)
                if configured - {s["endpoint"] for s in e["stash_ids"]}]
    log(f"Linking {len(entities)} {kind.name}s that are missing a stash-box ID")
    added = review = 0
    for i, entity in enumerate(entities):
        try:
            n, uncertain = link_entity(kind, stash, entity, boxes, review_tag, TASK_SEARCH_DELAY_SECONDS)
            added += n
            review += uncertain
        except (RuntimeError, urllib.error.URLError, OSError) as e:
            log(f"{kind.title} {entity.get('id')}: {e}", "warning")
        # Stash shows a "\x01p\x02<0..1>" line as the task's progress.
        print(f"\x01p\x02{(i + 1) / len(entities)}", file=sys.stderr, flush=True)
    summary = f"Added {added} stash-box IDs to {kind.name}s; {review} need review (tagged {review_tag!r})"
    log(summary)
    return summary


def main():
    plugin_input = json.loads(sys.stdin.read() or "{}")
    args = plugin_input.get("args") or {}
    stash = Stash(plugin_input.get("server_connection") or {})
    if args.get("hookContext"):
        output = run_hook(stash, args["hookContext"])
    else:
        output = run_task(stash, args.get("entity") or "studio")
    print(json.dumps({"output": output}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report to Stash rather than dying with a traceback
        log(f"{type(e).__name__}: {e}", "error")
        print(json.dumps({"error": str(e)}))
