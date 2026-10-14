# stash-plugins

Custom scrapers and plugins for [Stash](https://github.com/stashapp/stash).

- [Filename Parser](#filename-parser): a scene scraper that reads title, date, studio, performers and tags from the file path.
- [Folder Media](#folder-media-plugin): a plugin that uses a `cover`/`poster` image and a `teaser`/`preview`/`trailer` video next to a video as the scene's cover and preview.
- [Stash-box Linker](#stash-box-linker-plugin): a plugin that links studios and performers to the same entry on all your stash-boxes, e.g. StashDB and ThePornDB.
- [Scene Reset](#scene-reset-plugin): a plugin that adds **Reset metadata…** to the scene list menu, to undo a wrong Identify.

Each one has its own version number. Plugin versions show under **Settings > Plugins** in Stash, and the scraper's is in a comment at the bottom of its `.yml` file. To see what changed since the version you have, check [CHANGELOG.md](CHANGELOG.md).

## Installing

The plugins and the scraper are published as Stash **package sources**, so Stash can install them and tell you when there's an update.

**Plugins:**
1. In Stash, go to **Settings > Plugins > Available Plugins** and click **Add Source**.
2. Fill in:
   - **Name:** `stash-plugins`, or whatever you like.
   - **Source URL:** `https://in-me69.github.io/stash-plugins/main/index.yml`
   - **Local path:** `stash-plugins`. This is the folder inside your plugins directory where they're installed.
3. Expand the new source, tick the plugins you want, and click **Install**.

**Scraper:** the same steps, under **Settings > Metadata Providers > Available Scrapers > Add Source**, with this source URL:

```
https://in-me69.github.io/stash-plugins/main/scrapers/index.yml
```

**Updates:** use **Check for updates** under the installed plugins or scrapers. Each package's version is its version number plus the commit it was built from, for example `0.3.1-9236009`.

**Manual install:** the Install steps under each one below copy the folder by hand. Use them if you'd rather not add a source. Don't use both methods for the same package, or Stash loads it twice.

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
- Performers match on their primary name **and their aliases**. This differs from Auto Tag, which only uses primary names. Aliases shorter than 3 letters or digits, like `BB`, are never matched on their own, because they'd match ordinary words. Set `match_performer_aliases = false` to match primary names only. Names are matched against the filename exactly as written, so an alias like `bougie_bb` matches `bougie_bb`.
- When one performer's name only matches inside a longer matched name, the longer one wins. `Venus Afrodita` doesn't also match a performer called `Venus`, unless `Venus` also appears separately somewhere else in the path. This is one place where the scraper differs from Auto Tag, which would match both.
- Studios and tags match on name or alias.
- **Performer and tag names inside the studio's name don't count.** In `[California Star Productions] Girlfriends Revenge`, a performer called `Star` isn't matched. The same applies when the studio name appears in a folder name or is the series in a series-dated filename. A name that also appears separately elsewhere in the path still counts. So does a performer whose name *is* the whole studio name: performers who release their own content often have a studio named after them, so `[Bougie BB] Pool Day` gets both the studio and the performer `Bougie BB`.
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

Install it from the package source (see [Installing](#installing)). To install it by hand instead:

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
| `match_performer_aliases` | `true` | Also match performers by alias. `false` matches primary names only, like Auto Tag. |
| `match_scope` | `path` | `path` matches across the full path, like Auto Tag. `filename` ignores folder names. |
| `cache_ttl_seconds` | `300` | How long, in seconds, to cache the library lists. `0` turns the cache off. |

## Folder Media (plugin)

When a scan adds a new scene, this plugin looks in the same folder as the video and uses what it finds instead of letting Stash generate it:

| File in the folder | Becomes |
|---|---|
| `cover` or `poster` (any image extension) | the scene's **cover** |
| `teaser`, `preview` or `trailer` (any video extension) | the scene's **preview video**, the clip that plays when you hover over a scene |

### Rules

- **The video must be the only video in its folder.** Otherwise there's no telling which video the files belong to.
  - Teaser, preview and trailer files don't count as a second video.
  - Videos in subfolders don't count either.
  - Which extensions count as video or image comes from **Settings > Library**.
- **Names ignore case and extension.** `cover.jpg`, `Poster.PNG`, `Teaser.mp4` and `trailer.webm` all work.
  - When several names are present, `cover` beats `poster`, and `teaser` beats `preview`, which beats `trailer`.
  - You can change the names. See [Changing the names](#changing-the-names).
  - If a preview exists in several formats, `mp4`, `m4v` and `webm` are preferred because browsers play them.
- **Only the video's own folder is searched,** not subfolders.
- **Some scenes are skipped:** scenes made of several files, videos inside zip files, and a teaser that was imported as a scene of its own.

```
Pool Day/
├── Pool Day.mp4    ← the scene
├── cover.jpg       → cover
├── teaser.mp4      → preview video
└── Extras/
    └── Clip.mp4    (in a subfolder, so ignored)
```

### Stop Stash importing teasers as scenes

Stash scans every video, so `teaser.mp4` would also become a scene of its own. The plugin ignores teasers both when counting videos and as scenes, but it's tidier to tell Stash to skip them.

To do that, add this pattern under **Settings > Library > Excluded Video Patterns**:

```
(?i)[\\/](teaser|preview|trailer)\.[^\\/]+$
```

Teasers that were already imported can then be deleted from Stash. Choose not to delete the file when you do.

If you change the preview names (see below), update this pattern to match.

### Changing the names

The names are settings, under **Settings > Plugins > Folder Media**:

| Setting | Default |
|---|---|
| **Cover image names** | `cover, poster` |
| **Preview video names** | `teaser, preview, trailer` |

- **Order matters.** Each setting is a comma-separated list, in order of preference: earlier entries win when several files match.
- **Matching.** Each entry is matched against the **whole** file name, ignoring case and extension. A plain word matches only that name, so `cover` matches `Cover.jpg` but not `my cover.jpg`.
- **Patterns.** An entry can also be a regular expression. For example, `folder, cover, .*[-_ ]cover` also matches `folder.jpg` and `Scene-cover.jpg`.
- **Repeat counts are safe.** Commas inside `{m,n}` repeat counts don't split the list.
- **Bad patterns are skipped.** If a setting isn't a valid pattern, the plugin logs a warning and uses the default.
- **Preview names are never scenes.** Files matching the preview names never count as a second video in the folder.

### Previews: what to know

- **How the copy works.** Stash has no API for setting a preview. It keeps each scene's preview at `<generated folder>/screenshots/<file hash>.mp4`, so the plugin **copies** the teaser there. The hash is the oshash or MD5 hash, depending on Stash's file naming setting.
- **Disk space.** The copy uses as much space as the teaser itself.
- **Size and format.** Stash's own previews are short (about 9 seconds), small and silent. A full-size teaser copied as it is still works, but takes longer to start when you hover. Use MP4 with H.264 for the widest browser support; the plugin logs a warning for other formats. Better still, turn on **Shrink previews** (below).
- **Scrubber sprites** still come from the main video.

### Shrinking teasers

Turn on **Shrink previews** under **Settings > Plugins > Folder Media** to have teasers converted instead of copied:

- **Length:** only the first **Preview length** seconds are kept. The default is 10.
- **Size:** at most 640 px wide, the same as Stash's own previews. Smaller teasers aren't enlarged.
- **Format:** H.264 MP4, set up to start playing before it's fully loaded, using Stash's own preview encoding preset.
- **Sound:** kept only if Stash's own **preview audio** setting is on.

In a test with a 20-second 1080p teaser, the hover preview went from 12.5 MB to 0.5 MB.

- **ffmpeg:** shrinking uses the same ffmpeg as Stash. That's the one set in Stash's ffmpeg path setting if there is one, otherwise ffmpeg from the system path or the copy in Stash's config folder.
- **Fallbacks:** if ffmpeg isn't found, or can't read a teaser, the teaser is copied as it is and a warning is logged.
- **Scan time:** converting takes a few seconds per teaser, and it happens while the scan waits.

### Animated `.webp` previews

Stash can also make an animated `.webp` version of each preview. It does this when you tick **Image previews** in Generate or in the scan options. It's mainly used in the scene wall view.

- **Built from the teaser automatically.** Stash makes the `.webp` from the *preview video*, not from the main video. So once this plugin has put the teaser in place, Stash's normal image-preview generation turns the teaser into the `.webp`.
- **At scan time:** if image previews are ticked in your scan options, this happens automatically.
- **For existing scenes:** run **Generate** with **Image previews** ticked and **overwrite off**. With overwrite on, Stash first regenerates the video preview from the main video, replacing the teaser.
- **Stale files are removed.** Whenever the plugin replaces a preview, it deletes that scene's old `.webp`, so Generate rebuilds it from the new preview.
- **Keep them short.** Stash makes these `.webp` files lossless, so their size depends on length; in my test, 15 seconds of 640 px video came to almost 9 MB. If you use them, keep **Shrink previews** on with a short **Preview length**.

### How it avoids the generated versions

The plugin runs on Stash's `Scene.Create.Post` hook. For each new scene, Stash runs that hook *before* it queues cover and preview generation. Both generation steps skip a scene that already has a cover or preview. So whatever the plugin sets, Stash doesn't generate.

There's one exception: if a scan runs with Stash's **overwrite** option on, Stash regenerates everything and replaces the folder media.

The hook only runs for **newly added** scenes. For scenes already in Stash, run the **Set covers and previews from folder media** task from **Settings > Tasks > Plugin Tasks**. It applies the same rules to every scene, and **replaces** the current cover and preview wherever it finds folder media.

### Install

Install it from the package source (see [Installing](#installing)). To install it by hand instead:

1. Copy `plugins/FolderMedia/` into your Stash `plugins` folder. That folder is next to Stash's `config.yml`, or wherever **Settings > System > Plugins Path** points.
2. In Stash, go to **Settings > Plugins** and click **Reload plugins**. Make sure **Folder Media** is enabled.
3. Optionally, add the excluded pattern above.

It works without any configuration, using the default names. Stash gives plugins their own connection details, including your login session, so no URL or API key is needed. It uses only the Python standard library.

## Stash-box Linker (plugin)

When a studio or performer is linked to one stash-box, this plugin looks for the same studio or performer on every other stash-box configured in Stash, and adds those IDs too. For example, a studio added from StashDB gets its ThePornDB ID as well. It works in every direction between all your configured stash-boxes.

**Only the stash-box IDs are added.** Names, images and details aren't changed. You can pull those in later with Stash's Tagger if you want them.

### When it runs

- **When a studio or performer is created** with a stash-box ID, for example from the Tagger.
- **When a studio's or performer's stash-box IDs change.** Other edits don't trigger it, so they don't cause stash-box searches.
- **Studios and performers already in Stash:** run **Link studios across stash-boxes** or **Link performers across stash-boxes** from **Settings > Tasks > Plugin Tasks**. They pause briefly between searches to go easy on the stash-boxes.

Studios and performers without any stash-box ID are ignored.

### How it decides it's the same one

Stash itself searches each stash-box, using the API keys you've already set up. The plugin searches by name first, and by up to three aliases if the name finds nothing. A candidate is only considered if its name or one of its aliases matches the local name or an alias, ignoring case and punctuation.

**Studios** are linked when exactly one candidate matches. A candidate is ruled out if both sides have a parent studio and the parents differ. If several candidates remain, a shared website decides.

**Performers** need more, because performer names often collide.
- A candidate is ruled out if both sides have a birthdate, gender, country code or disambiguation and they differ. A year-only birthdate agrees with a full date in the same year.
- The performer is linked if exactly one remaining candidate **shares a birthdate or a link** (Twitter/X, Instagram, OnlyFans, website and so on).
- If nothing else can be compared, a single candidate is accepted only for a name of **two or more words**. A single-word name like "Lily" could be anyone.

An ID that's already linked to another studio or performer in Stash is never linked a second time.

### When it isn't sure

Nothing is linked. The studio or performer gets the tag **`Stash-box Match Needs Review`**, and the candidates are written to Stash's log as a warning. Filter by the tag to link them by hand. The next run that settles the entity removes the tag again, for example after you add the ID yourself.

You can change the tag name in the plugin's settings under **Settings > Plugins**.

### Install

Install it from the package source (see [Installing](#installing)). To install it by hand instead:

1. Copy `plugins/StashBoxLinker/` into your Stash `plugins` folder. That folder is next to Stash's `config.yml`, or wherever **Settings > System > Plugins Path** points.
2. In Stash, go to **Settings > Plugins** and click **Reload plugins**. Make sure **Stash-box Linker** is enabled.
3. Make sure the stash-boxes you want linked are set up under **Settings > Metadata Providers > Stash-box Endpoints**, with their API keys.

It needs no other configuration. It uses the connection details Stash gives plugins and the standard library only.

## Scene Reset (plugin)

Adds **Reset metadata…** to the scene list's menu, next to **Identify…** and **Generate…**, and to the **…** menu on each scene's own page. Use it when Identify (or a scraper) matched the wrong scene: it strips the metadata from the selected scenes so you can start again.

### What a reset does

| Cleared | Kept |
|---|---|
| title, studio code, details, director, URLs | the files themselves |
| date and production date | play count, history and resume point |
| studio, performers, tags, groups | O-count and rating |
| stash-box IDs, so a later Identify doesn't find the same wrong match by ID | markers and galleries |
| the **Organized** flag | |

- **The cover is reset.**
  - If the [Folder Media](#folder-media-plugin) plugin is installed and enabled, its rules run on each reset scene first. A `cover`/`poster` image next to the video becomes the cover again, and a `teaser`/`preview`/`trailer` video is copied back as the preview.
  - Scenes without a folder cover, or every scene when Folder Media isn't in use, get a fresh screenshot from Stash's **Generate**, with overwrite on.
  - Folder Media is found in the same plugins folder as Scene Reset, or one level down, such as `plugins/community/`. If it's enabled but can't be found, a warning is logged and screenshots are generated instead.
- **All tags are removed,** including ones you added yourself, because Stash doesn't record which tags came from Identify.
- **Nothing is deleted.** Performers, studios and tags are only removed from the scene, even if Identify created them and no scene uses them any more.
- **A reset can't be undone,** so it always asks you to confirm first.

### Using it

**Several scenes:**

1. In the scene list, select the scenes to reset. This works in the grid and list views.
2. Open the **…** menu and choose **Reset metadata…**, then confirm.
3. The reset runs as a task. You can follow it on the **Tasks** page, then refresh the list.

**One scene:** on the scene's own page, open the **…** menu next to the title and choose **Reset metadata…**. It sits under **Generate…**. When the task finishes, reload the page.

The plugin's **Reset scenes** task also appears under **Settings > Tasks > Plugin Tasks**. Run from there with no scenes selected, it does nothing, so it can't reset your whole library by accident.

**How the menu item is added:** Stash doesn't give plugins a way to add items to these menus, so the plugin's script watches the page and adds the item when a menu opens. On the scene list, it reads the selected scenes from the checked cards or rows. On a scene page, it takes the scene from the page address. A future Stash update that changes the scene list's layout could stop the item appearing. If that happens, nothing is reset by mistake; the item just doesn't show.

### Install

Install it from the package source (see [Installing](#installing)). To install it by hand instead:

1. Copy `plugins/SceneReset/` into your Stash `plugins` folder. That folder is next to Stash's `config.yml`, or wherever **Settings > System > Plugins Path** points.
2. In Stash, go to **Settings > Plugins** and click **Reload plugins**. Make sure **Scene Reset** is enabled.
3. Reload the Stash page in your browser so the menu script loads.

## Publishing

On every push to `main` that changes a plugin or the scraper, the **Publish to GitHub Pages** workflow does the following:
1. It runs the tests.
2. It runs `build_site.py`, which zips each package and writes the two `index.yml` files.
3. It publishes them to GitHub Pages.

A package's zip holds only the files git tracks in its folder, so local files like a `config.ini` or a cache are never published.

To build locally, run `python3 build_site.py`. The output goes to `_site/`.

`build_site.py` is based on Stash's [plugins-repo-template](https://github.com/stashapp/plugins-repo-template), extended to publish the scraper as well.

## Licence

[AGPL-3.0](LICENSE), the same licence as Stash's own community plugins and scrapers.

## Tests

Covers the scraper and the plugins' Python code:

```bash
python -m unittest discover -s tests -v
```
