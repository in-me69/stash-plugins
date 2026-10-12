import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "StashBoxLinker"))

import stash_box_linker as sbl  # noqa: E402
from stash_box_linker import decide_performer, decide_studio, normalize_url  # noqa: E402

STASHDB = "https://stashdb.org/graphql"
TPDB = "https://theporndb.net/graphql"


def studio(name, aliases=(), urls=(), parent=None, remote_site_id="r1"):
    return {"name": name, "aliases": list(aliases), "urls": list(urls), "parent": parent, "remote_site_id": remote_site_id}


def performer(name, aliases=(), urls=(), birthdate=None, gender=None, country=None, disambiguation=None,
              remote_site_id="r1"):
    return {"name": name, "aliases": list(aliases), "urls": list(urls), "birthdate": birthdate, "gender": gender,
            "country": country, "disambiguation": disambiguation, "remote_site_id": remote_site_id}


class StudioDecisionTest(unittest.TestCase):
    def test_single_name_match_links(self):
        d = decide_studio(studio("Alpha Beta Media"), [studio("AlphaBeta Media", remote_site_id="x")])
        self.assertEqual((d.outcome, d.candidate["remote_site_id"]), ("link", "x"))

    def test_alias_match_links(self):
        d = decide_studio(studio("ABM", aliases=["Alpha Beta Media"]), [studio("Alpha Beta Media")])
        self.assertEqual(d.outcome, "link")

    def test_no_name_match(self):
        self.assertEqual(decide_studio(studio("Alpha"), [studio("Alphabet")]).outcome, "none")
        self.assertEqual(decide_studio(studio("Alpha"), []).outcome, "none")

    def test_different_parent_is_uncertain(self):
        d = decide_studio(studio("Ladies Only", parent="Network A"), [studio("Ladies Only", parent="Network B")])
        self.assertEqual(d.outcome, "uncertain")
        # A parent on only one side isn't a contradiction.
        self.assertEqual(decide_studio(studio("Ladies Only"), [studio("Ladies Only", parent="B")]).outcome, "link")

    def test_several_matches_decided_by_website(self):
        candidates = [studio("Vixen", urls=["https://vixen.com"], remote_site_id="a"),
                      studio("Vixen", urls=["https://other.example"], remote_site_id="b")]
        d = decide_studio(studio("Vixen", urls=["http://www.vixen.com/"]), candidates)
        self.assertEqual((d.outcome, d.candidate["remote_site_id"]), ("link", "a"))
        self.assertEqual(decide_studio(studio("Vixen"), candidates).outcome, "uncertain")


class PerformerDecisionTest(unittest.TestCase):
    def test_full_name_single_match_links(self):
        d = decide_performer(performer("Venus Afrodita"), [performer("Venus Afrodita")])
        self.assertEqual(d.outcome, "link")

    def test_single_word_name_needs_corroboration(self):
        self.assertEqual(decide_performer(performer("Lily"), [performer("Lily")]).outcome, "uncertain")
        d = decide_performer(performer("Lily", birthdate="1995-02-03"), [performer("Lily", birthdate="1995-02-03")])
        self.assertEqual(d.outcome, "link")

    def test_corroboration_picks_between_namesakes(self):
        candidates = [performer("Anna Bell", urls=["https://twitter.com/annab"], remote_site_id="a"),
                      performer("Anna Bell", birthdate="1980-01-01", remote_site_id="b")]
        d = decide_performer(performer("Anna Bell", urls=["https://x.com/AnnaB"]), candidates)
        self.assertEqual((d.outcome, d.candidate["remote_site_id"]), ("link", "a"))
        self.assertEqual(decide_performer(performer("Anna Bell"), candidates).outcome, "uncertain")

    def test_contradictions_rule_out_a_candidate(self):
        local = performer("Anna Bell", birthdate="1990-05-01", gender="FEMALE", country="GB")
        for candidate in (performer("Anna Bell", birthdate="1991-05-01"),
                          performer("Anna Bell", gender="male"),
                          performer("Anna Bell", country="US")):
            self.assertEqual(decide_performer(local, [candidate]).outcome, "uncertain", candidate)
        # Partial dates, matching gender spelled differently, and country names aren't contradictions.
        ok = performer("Anna Bell", birthdate="1990", gender="Female", country="United Kingdom")
        self.assertEqual(decide_performer(local, [ok]).outcome, "link")

    def test_disambiguation_must_agree_when_both_have_one(self):
        local = performer("Anna Bell", disambiguation="UK")
        self.assertEqual(decide_performer(local, [performer("Anna Bell", disambiguation="US")]).outcome, "uncertain")
        self.assertEqual(decide_performer(local, [performer("Anna Bell")]).outcome, "link")


class UrlTest(unittest.TestCase):
    def test_normalize(self):
        for a, b in [("https://www.Twitter.com/Foo/", "twitter.com/foo"),
                     ("http://x.com/foo?ref=1", "twitter.com/foo"),
                     ("https://m.example.com/a#b", "example.com/a")]:
            self.assertEqual(normalize_url(a), b)


class FakeStash(BaseHTTPRequestHandler):
    """Answers the plugin's GraphQL with class-level state, recording updates."""
    entities = {}  # (kind, id) -> entity json
    searches = {}  # (endpoint, kind, query) -> [scraped json]
    tags = {}  # name -> id
    updates = []
    queries = []
    plugin_config = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body.get("variables") or {}
        FakeStash.queries.append(q)
        data = self.answer(q, v)
        out = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def answer(self, q, v):
        if "stashBoxes" in q:
            return {"configuration": {"general": {"stashBoxes": [
                {"endpoint": STASHDB, "name": "StashDB"}, {"endpoint": TPDB, "name": "TPDB"}]}}}
        if "plugins(" in q:
            return {"configuration": {"plugins": FakeStash.plugin_config}}
        for kind in ("studio", "performer"):
            title = kind.capitalize()
            if f"scrapeSingle{title}" in q:
                key = (v["s"]["stash_box_endpoint"], kind, v["i"]["query"])
                return {f"scrapeSingle{title}": FakeStash.searches.get(key, [])}
            if f"find{title}(" in q:
                return {f"find{title}": FakeStash.entities.get((kind, v["id"]))}
            if f"find{title}s(" in q:
                f = (v.get("f") or {}).get("stash_id_endpoint")
                rows = [e for (k, _), e in FakeStash.entities.items() if k == kind]
                if f:
                    rows = [e for e in rows if any(s["endpoint"] == f["endpoint"] and s["stash_id"] == f["stash_id"]
                                                   for s in e["stash_ids"])]
                else:  # NOT_NULL
                    rows = [e for e in rows if e["stash_ids"]]
                return {f"find{title}s": {f"{kind}s": rows}}
            if f"{kind}Update" in q:
                FakeStash.updates.append((kind, v["i"]))
                return {f"{kind}Update": {"id": v["i"]["id"]}}
        if "findTags" in q:
            name = v["f"]["name"]["value"]
            return {"findTags": {"tags": [{"id": FakeStash.tags[name], "name": name}] if name in FakeStash.tags else []}}
        if "tagCreate" in q:
            FakeStash.tags[v["i"]["name"]] = "t99"
            return {"tagCreate": {"id": "t99"}}
        raise AssertionError(f"unexpected query {q}")

    def log_message(self, *args):
        pass


def local_studio(id, name, stash_ids, tags=(), **kw):
    return {"id": id, "name": name, "aliases": kw.get("aliases", []), "urls": kw.get("urls", []),
            "parent_studio": kw.get("parent_studio"), "tags": list(tags),
            "stash_ids": [{"endpoint": e, "stash_id": s} for e, s in stash_ids]}


def local_performer(id, name, stash_ids, tags=(), **kw):
    return {"id": id, "name": name, "disambiguation": kw.get("disambiguation"), "alias_list": kw.get("aliases", []),
            "urls": kw.get("urls", []), "birthdate": kw.get("birthdate"), "gender": kw.get("gender"),
            "country": kw.get("country"), "tags": list(tags),
            "stash_ids": [{"endpoint": e, "stash_id": s} for e, s in stash_ids]}


def scraped(name, remote_site_id, **kw):
    return {"name": name, "aliases": kw.get("aliases"), "urls": kw.get("urls"), "parent": kw.get("parent"),
            "birthdate": kw.get("birthdate"), "gender": kw.get("gender"), "country": kw.get("country"),
            "disambiguation": kw.get("disambiguation"), "remote_site_id": remote_site_id}


class PluginRunTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), FakeStash)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        FakeStash.entities, FakeStash.searches, FakeStash.tags = {}, {}, {}
        FakeStash.updates, FakeStash.queries, FakeStash.plugin_config = [], [], {}
        sbl._tag_ids.clear()
        patcher = mock.patch.object(sbl, "TASK_SEARCH_DELAY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_plugin(self, args):
        plugin_input = {"server_connection": {"Host": "0.0.0.0", "Port": self.server.server_port}, "args": args}
        stdout, stderr = io.StringIO(), io.StringIO()
        sys_stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(plugin_input))
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                sbl.main()
        finally:
            sys.stdin = sys_stdin
        return json.loads(stdout.getvalue()), stderr.getvalue()

    def test_new_studio_from_stashdb_gets_tpdb_id(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "Alpha Beta Media", [(STASHDB, "sdb-1")])
        FakeStash.searches[(TPDB, "studio", "Alpha Beta Media")] = [scraped("Alpha Beta Media", "tpdb-9")]
        result, _ = self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Create.Post"}})
        self.assertEqual(FakeStash.updates, [("studio", {"id": "5", "stash_ids": [
            {"endpoint": STASHDB, "stash_id": "sdb-1"}, {"endpoint": TPDB, "stash_id": "tpdb-9"}]})])
        self.assertEqual(result["output"], "Added 1 stash-box IDs")

    def test_works_in_the_other_direction_too(self):
        FakeStash.entities[("performer", "3")] = local_performer("3", "Venus Afrodita", [(TPDB, "tpdb-3")])
        FakeStash.searches[(STASHDB, "performer", "Venus Afrodita")] = [scraped("Venus Afrodita", "sdb-3")]
        self.run_plugin({"hookContext": {"id": 3, "type": "Performer.Create.Post"}})
        self.assertEqual(FakeStash.updates[0][1]["stash_ids"][-1], {"endpoint": STASHDB, "stash_id": "sdb-3"})

    def test_falls_back_to_alias_search(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "ABM", [(STASHDB, "s")], aliases=["Alpha Beta Media"])
        FakeStash.searches[(TPDB, "studio", "Alpha Beta Media")] = [scraped("Alpha Beta Media", "tpdb-9")]
        self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Create.Post"}})
        self.assertEqual(FakeStash.updates[0][1]["stash_ids"][-1]["stash_id"], "tpdb-9")

    def test_uncertain_match_is_tagged_not_linked(self):
        FakeStash.entities[("performer", "3")] = local_performer("3", "Lily", [(STASHDB, "s")], tags=[{"id": "1", "name": "Blonde"}])
        FakeStash.searches[(TPDB, "performer", "Lily")] = [scraped("Lily", "a"), scraped("Lily", "b")]
        result, stderr = self.run_plugin({"hookContext": {"id": 3, "type": "Performer.Create.Post"}})
        self.assertEqual(FakeStash.updates, [("performer", {"id": "3", "tag_ids": ["1", "t99"]})])
        self.assertEqual(FakeStash.tags, {"Stash-box Match Needs Review": "t99"})
        self.assertIn("needs review", result["output"])
        self.assertIn("\x01w\x02", stderr)

    def test_review_tag_name_from_plugin_settings_and_existing_tag_reused(self):
        FakeStash.plugin_config = {"StashBoxLinker": {"reviewTag": "Check Me"}}
        FakeStash.tags = {"Check Me": "t5"}
        FakeStash.entities[("performer", "3")] = local_performer("3", "Lily", [(STASHDB, "s")])
        FakeStash.searches[(TPDB, "performer", "Lily")] = [scraped("Lily", "a")]
        self.run_plugin({"hookContext": {"id": 3, "type": "Performer.Create.Post"}})
        self.assertEqual(FakeStash.updates, [("performer", {"id": "3", "tag_ids": ["t5"]})])

    def test_review_tag_removed_once_resolved(self):
        FakeStash.entities[("studio", "5")] = local_studio(
            "5", "Alpha", [(STASHDB, "s")], tags=[{"id": "t99", "name": "Stash-box Match Needs Review"}])
        FakeStash.searches[(TPDB, "studio", "Alpha")] = [scraped("Alpha", "tpdb-1")]
        self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Update.Post", "inputFields": ["id", "stash_ids"]}})
        self.assertEqual(FakeStash.updates[0][1]["tag_ids"], [])

    def test_not_found_changes_nothing(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "Alpha", [(STASHDB, "s")])
        result, _ = self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Create.Post"}})
        self.assertEqual(FakeStash.updates, [])
        self.assertEqual(result["output"], "Added 0 stash-box IDs")

    def test_id_already_used_by_another_entity_is_not_linked_twice(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "Alpha", [(STASHDB, "s")])
        FakeStash.entities[("studio", "6")] = local_studio("6", "Alpha Old", [(TPDB, "tpdb-1")])
        FakeStash.searches[(TPDB, "studio", "Alpha")] = [scraped("Alpha", "tpdb-1")]
        self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Create.Post"}})
        self.assertEqual(FakeStash.updates, [("studio", {"id": "5", "tag_ids": ["t99"]})])

    def test_ignores_entities_without_any_stash_id(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "Alpha", [])
        self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Create.Post"}})
        self.assertFalse(any("scrapeSingle" in q for q in FakeStash.queries))

    def test_ignores_updates_that_dont_touch_stash_ids(self):
        FakeStash.entities[("studio", "5")] = local_studio("5", "Alpha", [(STASHDB, "s")])
        result, _ = self.run_plugin({"hookContext": {"id": 5, "type": "Studio.Update.Post", "inputFields": ["id", "details"]}})
        self.assertEqual(result["output"], "Stash-box IDs unchanged")
        self.assertFalse(any("scrapeSingle" in q for q in FakeStash.queries))

    def test_task_links_every_entity_missing_a_box(self):
        FakeStash.entities[("studio", "1")] = local_studio("1", "Alpha", [(STASHDB, "s1")])
        FakeStash.entities[("studio", "2")] = local_studio("2", "Beta", [(STASHDB, "s2"), (TPDB, "t2")])  # done
        FakeStash.entities[("studio", "3")] = local_studio("3", "Gamma", [])  # never matched
        FakeStash.searches[(TPDB, "studio", "Alpha")] = [scraped("Alpha", "t1")]
        result, stderr = self.run_plugin({"entity": "studio"})
        self.assertEqual([u[1]["id"] for u in FakeStash.updates], ["1"])
        self.assertIn("Added 1 stash-box IDs to studios; 0 need review", result["output"])
        self.assertIn("\x01p\x021.0", stderr)


if __name__ == "__main__":
    unittest.main()
