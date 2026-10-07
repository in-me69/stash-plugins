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
- When one performer's name only matches inside a longer matched name, the longer one wins. `Venus Afrodita` doesn't also match a performer called `Venus`, unless `Venus` also appears separately somewhere else in the path. This is one place where the scraper differs from Auto Tag, which would match both.
- Studios and tags match on name or alias.
- **Performer and tag names inside the studio's name don't count.** In `[California Star Productions] Girlfriends Revenge`, a performer called `Star` isn't matched. The same applies when the studio name appears in a folder name or is the series in a series-dated filename. A name that also appears separately elsewhere in the path still counts.
- **A studio in a leading `[…]` takes priority.** For `[Alpha Beta Media] Diamond Collection 276 …`, the studio is `Alpha Beta Media`, even if another studio such as `Diamond Collection` matches further right. Auto Tag would pick the rightmost match.
  - The bracket must match a studio's whole name or alias. Spacing and punctuation are ignored, so `[Alpha Beta Media]` matches `AlphaBeta Media`.
  - If no studio in Stash matches, the bracket text is still returned as the studio, so you can create or link it from the scrape dialog.
  - A leading bracket that holds only a date or quality marker (`[1080p]`), or only performer names, isn't treated as a studio.
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

The scraper removes the following from the filename:

- dates
- resolution and quality markers: `1080p`, `4K`, `1920x1080`, `HDR`, `60fps`
- codecs: `x264`, `HEVC`, `AV1`, `10bit`, `AAC`
- source markers: `WEB-DL`, `BluRay`, `XXX`, `MP4`
- trailing release-group suffixes like `-GRP`

**Performer and studio names are only removed when they're set apart from the title.** The filename is split into segments at clear separators:

- ` - ` (or `–`, `—`, `|`) with spaces on both sides
- anything in brackets: `[…]`, `(…)` or `{…}`
- the place where a date or quality marker was removed

A segment is dropped only if it contains nothing but matched performer or studio names, plus joining words or symbols (`,` `&` `+` `/` `and` `with` `feat.` `ft.` `vs` `x`). Names that are part of the title are left alone.

| Filename | Title |
|---|---|
| `[Studio] Alice, Bob - Pool Day` | `Pool Day` |
| `[Studio] Pool Day - Alice & Bob` | `Pool Day` |
| `Pool Day (Alice Smith) 1080p` | `Pool Day` |
| `Dinner with Alice Smith` | `Dinner with Alice Smith` |
| `Studio.2023.04.15.Alice.Smith.Pool.Day` | `Alice Smith Pool Day` (nothing separates the name from the title) |
| `Pool Day - Alice, Zed` (Zed not in Stash) | `Pool Day - Alice, Zed` |

Performers and studios are still returned whether or not their names were removed from the title.

Tag names are never removed from the title, because words like "Beach" are often part of the real title.

After that, the scraper removes empty or unmatched brackets, duplicate separators, and stray punctuation at either end of the title. Words like "with" or "and" are only removed as part of a dropped name segment, never from the title itself.

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
2. If Stash has a login, make sure it has an API key: generate one under **Settings > Security** if you haven't already.
3. In Stash, go to **Settings > Metadata Providers > Scrapers** and click **Reload scrapers**.
4. Open a scene, click **Edit**, then **Scrape with… > Filename Parser**. To scrape in bulk, add **Filename Parser** as a source in the **Identify** task.

#### Connecting to Stash

Usually there's nothing to configure. Stash doesn't pass its address or API key to scraper scripts, so the scraper finds them itself. It uses the first of these that sets an API key or a non-default URL:

1. The `STASH_URL` / `STASH_API_KEY` environment variables.
2. This scraper's own `config.ini`.
3. **py_common's `config.ini`**, if you use [CommunityScrapers](https://github.com/stashapp/CommunityScrapers). The URL and key you already set there for your other scrapers are reused. The scraper finds py_common next to its own folder or under `community/`, where Stash's scraper manager installs it. It only reads that file, so py_common's Python packages aren't needed.
4. **Stash's own `config.yml`.** The scraper looks in the folders above it (Stash's default layout puts `scrapers/` next to `config.yml`), then in `~/.stash/` and `/root/.stash/` (the Docker image). It picks up the port, the API key and whether Stash uses HTTPS. You can also point it at the file with the `STASH_CONFIG_FILE` environment variable.
5. `http://localhost:9999` with no API key.

The URL and key always come from the same place, so a key is never sent to a server it wasn't set for. The Stash log shows which file was used.

You only need this scraper's own `config.ini` if none of these work for you. For example, Stash runs on another machine and you don't use py_common. In that case, copy `config.example.ini` to `config.ini` and set `url` and `api_key`.

The scraper uses only the Python standard library, so nothing needs to be installed with pip. It runs with whatever Python Stash is configured with (**Settings > System > Python executable path**).

The scraper needs to reach Stash's GraphQL API to read your performers, studios and tags. Those lists are cached for `cache_ttl_seconds` (default 5 minutes). If you add a performer and want the next scrape to see it right away, delete `.library-cache.json`.

### Configuration

`config.ini` is optional. All settings are documented in [`config.example.ini`](scrapers/FilenameParser/config.example.ini):

| Setting | Default | What it does |
|---|---|---|
| `url` | detected (see above), else `http://localhost:9999` | Stash address. Can be overridden by the `STASH_URL` environment variable. |
| `api_key` | detected (see above), else empty | Stash API key. Can be overridden by the `STASH_API_KEY` environment variable. |
| `review_tag` | `Date Needs Review` | Tag added to scenes with ambiguous dates. Leave empty to disable it. |
| `two_digit_year_order` | `auto` | `auto` treats dates like `23.04.15` as ambiguous. `ymd` always reads them as YY.MM.DD. |
| `match_scope` | `path` | `path` matches across the full path, like Auto Tag. `filename` ignores folder names. |
| `cache_ttl_seconds` | `300` | How long, in seconds, to cache the library lists. `0` turns the cache off. |

### Tests

```bash
python -m unittest discover -s tests -v
```
