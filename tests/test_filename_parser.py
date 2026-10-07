import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scrapers", "FilenameParser"))

from FilenameParser import (  # noqa: E402
    Entity, Library, choose_date, find_dates, find_name, scrape_path,
)

LIBRARY = Library(
    performers=[
        Entity("Alice Smith", ["Ally S"]),
        Entity("Bob Jones"),
        Entity("Carol"),
        Entity("Dee Dee"),
    ],
    studios=[
        Entity("Example Studio", ["ExStudio"]),
        Entity("Other Site"),
    ],
    tags=[
        Entity("Outdoor", ["Outside"]),
        Entity("Beach"),
    ],
)


def names(result, key):
    return [x["name"] for x in result.get(key, [])]


class NameMatchingTest(unittest.TestCase):
    """Mirrors Stash's Auto Tag rules (pkg/match/path.go)."""

    def test_spaces_match_any_separator_or_none(self):
        for text in ("alice smith", "Alice.Smith", "alice_smith", "alice-smith", "AliceSmith", "alice . smith"):
            self.assertTrue(find_name("Alice Smith", text), text)

    def test_requires_word_boundaries(self):
        self.assertFalse(find_name("Carol", "Caroline"))
        self.assertFalse(find_name("Carol", "xcarol"))
        self.assertTrue(find_name("Carol", "_carol_"))
        self.assertTrue(find_name("Carol", "(Carol)"))

    def test_performer_aliases_are_not_matched(self):
        result = scrape_path("/v/Ally S in the park.mp4", LIBRARY)
        self.assertEqual(names(result, "performers"), [])

    def test_studio_rightmost_match_wins_and_aliases_count(self):
        result = scrape_path("/v/Other Site/ExStudio - A Title.mp4", LIBRARY)
        self.assertEqual(result["studio"], {"name": "Example Studio"})

    def test_tags_match_name_or_alias_across_path(self):
        result = scrape_path("/v/Outside/Fun at the Beach.mp4", LIBRARY)
        self.assertEqual(sorted(names(result, "tags")), ["Beach", "Outdoor"])

    def test_filename_scope_ignores_directories(self):
        result = scrape_path("/v/Outside/Fun.mp4", LIBRARY, {"match_scope": "filename"})
        self.assertEqual(names(result, "tags"), [])


class DateTest(unittest.TestCase):
    def date_of(self, text, **opts):
        return choose_date(find_dates(text, **opts))

    def test_year_first(self):
        self.assertEqual(self.date_of("x 2023.04.05 y"), ("2023-04-05", False))
        self.assertEqual(self.date_of("x 2023-4-5 y"), ("2023-04-05", False))
        self.assertEqual(self.date_of("x 20230405 y"), ("2023-04-05", False))

    def test_unambiguous_day_first_or_month_first(self):
        self.assertEqual(self.date_of("13.04.2023"), ("2023-04-13", False))
        self.assertEqual(self.date_of("04.13.2023"), ("2023-04-13", False))
        self.assertEqual(self.date_of("05.05.2023"), ("2023-05-05", False))

    def test_ambiguous_keeps_year_and_flags_review(self):
        self.assertEqual(self.date_of("03.04.2023"), ("2023", True))

    def test_two_digit_year(self):
        # 23.04.15 could be 2023-04-15 or 2015-04-23: different years.
        self.assertEqual(self.date_of("Site.23.04.15.Title"), (None, True))
        self.assertEqual(self.date_of("Site.23.04.15.Title", two_digit_year_order="ymd"), ("2023-04-15", False))
        # Only one valid reading (31 can't be a day-first/month-first year >= 1950).
        self.assertEqual(self.date_of("Site.25.12.31.Title"), ("2025-12-31", False))

    def test_month_names(self):
        self.assertEqual(self.date_of("15th April 2023"), ("2023-04-15", False))
        self.assertEqual(self.date_of("Apr 15, 2023"), ("2023-04-15", False))
        self.assertEqual(self.date_of("September 2021"), ("2021-09", False))

    def test_year_only(self):
        self.assertEqual(self.date_of("Title (2019)"), ("2019", False))
        self.assertEqual(self.date_of("Title 1850"), (None, False))

    def test_unambiguous_date_beats_ambiguous_one(self):
        self.assertEqual(self.date_of("03.04.2023 2023-04-03"), ("2023-04-03", False))

    def test_resolution_is_not_a_year(self):
        self.assertEqual(self.date_of("Title 1920x1080"), (None, False))


class GenericScrapeTest(unittest.TestCase):
    def test_strips_noise_names_and_dates(self):
        result = scrape_path(
            "/videos/Example Studio/Alice Smith & Bob Jones - Beach Day (2021) [1080p] [x264].mp4", LIBRARY
        )
        self.assertEqual(result["title"], "Beach Day")
        self.assertEqual(result["date"], "2021")
        self.assertEqual(result["studio"], {"name": "Example Studio"})
        self.assertEqual(names(result, "performers"), ["Alice Smith", "Bob Jones"])
        self.assertEqual(names(result, "tags"), ["Beach"])

    def test_dotted_scene_release(self):
        result = scrape_path(
            "/dl/ExStudio.2022.11.03.Alice.Smith.A.Long.Afternoon.XXX.2160p.HEVC.MP4-GRP.mkv", LIBRARY
        )
        self.assertEqual(result["title"], "A Long Afternoon")
        self.assertEqual(result["date"], "2022-11-03")
        self.assertEqual(result["studio"], {"name": "Example Studio"})
        self.assertEqual(names(result, "performers"), ["Alice Smith"])

    def test_ambiguous_date_adds_review_tag(self):
        result = scrape_path("/v/A Title 03-04-2020 720p.mp4", LIBRARY)
        self.assertEqual(result["title"], "A Title")
        self.assertEqual(result["date"], "2020")
        self.assertEqual(names(result, "tags"), ["Date Needs Review"])

    def test_review_tag_name_is_configurable_and_can_be_disabled(self):
        result = scrape_path("/v/A Title 03-04-2020.mp4", LIBRARY, {"review_tag": "Check Date"})
        self.assertEqual(names(result, "tags"), ["Check Date"])
        result = scrape_path("/v/A Title 03-04-2020.mp4", LIBRARY, {"review_tag": ""})
        self.assertNotIn("tags", result)

    def test_leftover_brackets_and_joiners_removed(self):
        result = scrape_path("/v/Fun Times (Alice Smith, Carol) [] {HD} - with Dee Dee.mp4", LIBRARY)
        self.assertEqual(result["title"], "Fun Times")
        self.assertEqual(names(result, "performers"), ["Alice Smith", "Carol", "Dee Dee"])

    def test_title_falls_back_when_only_names_remain(self):
        result = scrape_path("/v/Alice Smith 1080p.mp4", LIBRARY)
        self.assertEqual(result["title"], "Alice Smith")

    def test_keeps_tag_words_in_title(self):
        result = scrape_path("/v/Outdoor Adventure.mp4", LIBRARY)
        self.assertEqual(result["title"], "Outdoor Adventure")

    def test_keeps_meaningful_dots_and_hyphens(self):
        result = scrape_path("/v/Mr. Smith's Well-Known Secret 4K.mp4", LIBRARY)
        self.assertEqual(result["title"], "Mr. Smith's Well-Known Secret")

    def test_windows_paths(self):
        result = scrape_path(r"C:\media\Other Site\Something 2020-01-02.mp4", LIBRARY)
        self.assertEqual(result["title"], "Something")
        self.assertEqual(result["studio"], {"name": "Other Site"})


class SeriesDatedNameTest(unittest.TestCase):
    def test_current_order(self):
        result = scrape_path(
            "/lib/Example Studio/[Example Studio] 2024.02.29 Part 1 - The Start - Alice Smith, New Person [WEBDL-1080p, h264].mp4",
            LIBRARY,
        )
        self.assertEqual(result["title"], "Part 1 - The Start")
        self.assertEqual(result["date"], "2024-02-29")
        self.assertEqual(result["studio"], {"name": "Example Studio"})
        self.assertEqual(names(result, "performers"), ["Alice Smith", "New Person"])

    def test_unknown_series_and_case_normalised_performers(self):
        result = scrape_path("/lib/[Brand New] 2023.01.02 Title - alice smith.mp4", LIBRARY)
        self.assertEqual(result["studio"], {"name": "Brand New"})
        self.assertEqual(names(result, "performers"), ["Alice Smith"])

    def test_legacy_order(self):
        result = scrape_path("/lib/[Other Site] 2020.05.06 Bob Jones, Carol - A Title [WEBRIP-720p, hevc].mp4", LIBRARY)
        self.assertEqual(result["title"], "A Title")
        self.assertEqual(names(result, "performers"), ["Bob Jones", "Carol"])

    def test_truncated_performer_list(self):
        result = scrape_path("/lib/[Other Site] 2020.05.06 Title - Alice Smith, Bob Jones +3.mp4", LIBRARY)
        self.assertEqual(result["title"], "Title")
        self.assertEqual(names(result, "performers"), ["Alice Smith", "Bob Jones"])
        result = scrape_path("/lib/[Other Site] 2020.05.06 Title - +3.mp4", LIBRARY)
        self.assertEqual(result["title"], "Title")
        self.assertNotIn("performers", result)

    def test_no_performers(self):
        result = scrape_path("/lib/[Other Site] 2020.05.06 Just A Title [WEBDL-480p, h264].mp4", LIBRARY)
        self.assertEqual(result["title"], "Just A Title")
        self.assertNotIn("performers", result)


if __name__ == "__main__":
    unittest.main()
