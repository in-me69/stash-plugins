# stash-scrapers

Custom scrapers for [Stash](https://github.com/stashapp/stash).

## Filename Parser

A scene scraper that fills in metadata from the scene's file path:

| Field | Where it comes from |
|---|---|
| Performers | Performers in your library whose name appears in the path |
| Studio | The studio whose name or alias appears furthest right in the path |
| Tags | Tags whose name or alias appears in the path |
| Date | A full date from the filename, or a year-month or year if that's all there is |
| Title | What's left of the filename once those parts are removed |

### Matching rules

Performers, studios and tags are matched the same way Stash's **Auto Tag** task matches them:

- A space in a name matches `.`, `-`, `_`, a space, or nothing in the path. So `Alice Smith` matches `Alice.Smith`, `alice_smith` and `AliceSmith`.
- A name only matches as a whole word. `Carol` doesn't match `Caroline`.
- Performers match on their primary name only. Auto Tag has performer alias matching turned off, and this scraper does the same.
- Studios and tags match on name or alias.
- Anything marked **Ignore auto tag** in Stash is skipped.

The difference from Auto Tag is that this is a scraper. Results appear in Stash's scrape dialog for you to review before saving. It also works with the **Identify** task.

### Dates

Dates are read from the filename only, not from folder names. Supported formats:

- `2023.04.15`, `2023-04-15`, `2023_4_15`, `20230415`
- `15.04.2023` and `04.15.2023`, when only one reading is valid
- `15th April 2023`, `Apr 15, 2023`
- `April 2023` and `2023-04`, saved as year-month
- `2023` or `(2023)`, saved as year only

Stash supports year-only and year-month dates.

**Ambiguous dates:** some dates can be read more than one way, for example `03.04.2023` (3 April or March 4). For these, the scraper keeps only the year and adds the tag **`Date Needs Review`**. You can then filter on that tag to fix dates by hand. If the possible readings don't share a year, no date is set and the tag is still added. This can happen with two-digit years like `23.04.15`.

### Title cleanup

The scraper removes the following from the filename and uses what's left as the title:

- dates
- resolution and quality markers: `1080p`, `4K`, `1920x1080`, `HDR`, `60fps`
- codecs: `x264`, `HEVC`, `AV1`, `10bit`, `AAC`
- source markers: `WEB-DL`, `BluRay`, `XXX`, `MP4`
- trailing release-group suffixes like `-GRP`
- names of matched performers and studios

After that it removes empty or unmatched brackets, leftover joiners such as `&`, `and`, `with` or `feat.`, and duplicate separators.

Tag names are **not** removed from the title, because words like "Beach" are often part of the real title.

If the filename uses dots or underscores between words (`Some.Scene.Name`), these become spaces. If removing names would leave nothing, the title keeps the names.

### Series-dated filenames

The scraper recognizes this filename format and parses it exactly:

```
[Series] YYYY.MM.DD Title - Performer1, Performer2 [WEBDL-1080p, h264].mp4
```

- The series becomes the studio. Even a series that isn't a studio in Stash yet is returned, so you can create it from the scrape dialog.
- The performer list comes from the filename, including performers not yet in Stash. A truncated list such as `Alice, Bob +3` is handled.
- A title containing ` - ` is kept intact.
- The older `Performers - Title` order is also recognized. The scraper picks whichever side matches more existing performers.
- Auto Tag–style matches from the rest of the path are added as well.

### Install

1. Copy `scrapers/FilenameParser/` into your Stash `scrapers` directory. This directory is next to Stash's `config.yml`, or set under **Settings > System > Scrapers Path**.
2. Copy `config.example.ini` to `config.ini` in the same folder. Set `url` and, if Stash has a login, `api_key` (found under **Settings > Security**).
3. In Stash, go to **Settings > Metadata Providers > Scrapers** and click **Reload scrapers**.
4. Open a scene, click **Edit**, then **Scrape with… > Filename Parser**. To scrape in bulk, add **Filename Parser** as a source in the **Identify** task.

The scraper uses only the Python standard library, so nothing needs to be installed with pip. It runs with whatever Python Stash is configured with (**Settings > System > Python executable path**).

The scraper needs to reach Stash's GraphQL API to read your performers, studios and tags. Those lists are cached for `cache_ttl_seconds` (default 5 minutes). If you add a performer and want the next scrape to see it right away, delete `.library-cache.json`.

### Configuration

All settings are documented in [`config.example.ini`](scrapers/FilenameParser/config.example.ini):

| Setting | Default | What it does |
|---|---|---|
| `url` | `http://localhost:9999` | Stash address. Can be overridden by the `STASH_URL` environment variable. |
| `api_key` | empty | Stash API key. Can be overridden by the `STASH_API_KEY` environment variable. |
| `review_tag` | `Date Needs Review` | Tag added to scenes with ambiguous dates. Leave empty to disable it. |
| `two_digit_year_order` | `auto` | `auto` treats dates like `23.04.15` as ambiguous. `ymd` always reads them as YY.MM.DD. |
| `match_scope` | `path` | `path` matches across the full path, like Auto Tag. `filename` ignores folder names. |
| `cache_ttl_seconds` | `300` | How long, in seconds, to cache the library lists. `0` turns the cache off. |

### Tests

```bash
python -m unittest discover -s tests -v
```
