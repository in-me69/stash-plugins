# Changelog

Each scraper and plugin has its own version. A plugin's version is shown under **Settings > Plugins** in Stash. The scraper's version is in a comment at the bottom of `FilenameParser.yml`.

To see what changed since the version you have installed, compare it with the entries below.

## Filename Parser (scraper)

### 0.7.1 — 2026-10-14
- Text that is an alias of several performers, such as a folder `Gracie`, no longer adds all of them. A performer whose own name it is wins. Otherwise none of them is added, unless one also matched elsewhere in the path, and the scene gets the new `performer_review_tag` (`Performer Needs Review`).
- A performer with the studio's name as an alias, such as the site they were credited on, is no longer added to every scene from that studio. A performer whose own name is the studio's name still is.
- The log shows what each performer was matched on, and which alias, so an unexpected match can be traced.

### 0.7.0 — 2026-10-08 (#6)
- Performers are also matched by their aliases. The new `match_performer_aliases` setting turns this off, and aliases shorter than 3 characters are never matched.
- A performer whose name is the whole studio name, such as a performer with their own studio, is kept.
- Names with underscores, like `bougie_bb`, are matched as written and removed from the title correctly.

### 0.6.0 — 2026-10-07 (#5)
- Performer and tag matches found only inside the studio's name are ignored. For example, `Star` is no longer matched inside `[California Star Productions]`.
- Log lines use Stash's log levels, so they no longer all show as errors.

### 0.5.0 — 2026-10-07 (#4)
- A leading `[Studio]` in the filename is used as the studio. It wins over other studio matches, and is used even if that studio isn't in Stash yet.

### 0.4.0 — 2026-10-07 (#3)
- When performer names overlap, the longest wins. For example, `Venus Afrodita` no longer also matches a performer called `Venus`.

### 0.3.0 — 2026-10-07 (#2)
- Performer and studio names are only removed from the title when they're set apart from it, such as in `[Studio] Alice, Bob - Title`. Words like "with" are no longer stripped from the start or end of titles.

### 0.2.0 — 2026-10-07 (#1)
- The Stash URL and API key are found automatically, from py_common's `config.ini` or Stash's own `config.yml`.

### 0.1.0 — 2026-10-07
- First version. It matches performers, studios and tags the way Auto Tag does, reads the date, cleans up the title, and parses series-dated filenames exactly.

## Folder Media (plugin)

### 0.3.1 — 2026-10-14
- The project moved to https://github.com/in-me69/stash-plugins. Nothing else changed; reinstall from there.

### 0.3.0 — 2026-10-14 (#12)
- A new **Shrink previews** setting, off by default. Teasers are converted with ffmpeg into short, small previews instead of being copied as they are: the first **Preview length** seconds (default 10), at most 640 px wide, H.264, with sound only if Stash's preview audio setting is on. If ffmpeg is missing or fails, the teaser is copied as before.
- When a preview is replaced, the scene's old animated `.webp` is deleted, so Stash's **Generate** rebuilds it from the new preview.

### 0.2.0 — 2026-10-12 (#11)
- The cover and preview file names are now settings, as comma-separated names or patterns in order of preference.

### 0.1.0 — 2026-10-12 (#7)
- First version. A `cover`/`poster` image and a `teaser`/`preview`/`trailer` video next to a newly scanned video become its cover and preview. A task applies the same rules to existing scenes.

## Scene Reset (plugin)

### 0.3.2 — 2026-10-14 (#2)
- Fixed every reset failing with "HTTP Error 422: Unprocessable Entity". The reset cleared `production_date`, which no Stash release has yet. Fields your Stash version doesn't have are now left out.
- A failed request logs Stash's reason instead of just the HTTP status.

### 0.3.1 — 2026-10-14
- The project moved to https://github.com/in-me69/stash-plugins. Nothing else changed; reinstall from there.

### 0.3.0 — 2026-10-12 (#11)
- **Reset metadata…** is also in the **…** menu on each scene's own page.

### 0.2.0 — 2026-10-12 (#10)
- If Folder Media is installed and enabled, a reset restores the folder cover and preview instead of generating a screenshot.

### 0.1.0 — 2026-10-12 (#9)
- First version. **Reset metadata…** in the scene list menu clears the selected scenes' metadata and regenerates their covers.

## Stash-box Linker (plugin)

### 0.1.1 — 2026-10-14
- The project moved to https://github.com/in-me69/stash-plugins. Nothing else changed; reinstall from there.

### 0.1.0 — 2026-10-12 (#8)
- First version. When a studio or performer is linked to one stash-box, the plugin finds the same one on your other stash-boxes and adds those IDs. Matches it isn't sure of get a review tag instead.
