#!/usr/bin/env python3
"""Builds the Stash package source indexes published to GitHub Pages.

Based on Stash's plugins-repo-template (build_site.sh), extended to publish
the scrapers too. Output, under OUTDIR (default "_site"):

    index.yml            plugins source index (Settings > Plugins > Available)
    <PluginId>.zip
    scrapers/index.yml   scrapers source index (Metadata Providers > Available)
    scrapers/<ScraperId>.zip

Each package is a folder under plugins/ or scrapers/ holding <Id>.yml. Its
zip contains the folder's git-tracked files only, so local files such as a
config.ini or a cache are never published. Its version is the version
number in the .yml ("version:" for plugins, a "# Version:" comment for
scrapers, which can't have a version field), followed by the short hash of
the last commit touching the folder, as the template does. A
"# requires: Id1, Id2" comment in the .yml lists packages it depends on.

Index values are written quoted, so descriptions containing quotes or
colons stay valid YAML.

Usage: python3 build_site.py [OUTDIR]

Licensed under AGPL-3.0, like the template it's based on.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
# A fixed timestamp for zip entries, so an unchanged package always builds to
# the same bytes (and sha256).
ZIP_TIMESTAMP = (2000, 1, 1, 0, 0, 0)


def git(*args):
    # TZ=UTC0: commit dates in the index are UTC, as in the template.
    env = {**os.environ, "TZ": "UTC0"}
    return subprocess.run(["git", *args], cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout


def top_level_value(text, key):
    """The value of a top-level "key: value" line in a YAML file, unquoted."""
    m = re.search(rf"^{key}:[ \t]*(.*?)[ \t]*$", text, re.M)
    if not m:
        return None
    value = m.group(1)
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def comment_value(text, key):
    m = re.search(rf"^#[ \t]*{key}:[ \t]*(.*?)[ \t]*$", text, re.M | re.I)
    return m.group(1) if m else None


def package_info(folder, kind):
    """Index entry fields for the package in `folder` (relative to ROOT)."""
    package_id = os.path.basename(folder)
    with open(os.path.join(ROOT, folder, f"{package_id}.yml"), encoding="utf-8") as f:
        text = f.read()
    number = top_level_value(text, "version") if kind == "plugins" else comment_value(text, "Version")
    commit, date = git("log", "-n", "1", "--date=format-local:%F %T", "--pretty=format:%h|%ad", "--", folder).split("|")
    entry = {
        "id": package_id,
        "name": top_level_value(text, "name") or package_id,
        "version": f"{number}-{commit}" if number else commit,
        "date": date,
        "path": f"{package_id}.zip",
    }
    description = top_level_value(text, "description")
    if description:
        entry["metadata"] = {"description": description}
    requires = comment_value(text, "requires")
    if requires:
        entry["requires"] = [r.strip() for r in requires.split(",") if r.strip()]
    return entry


def build_zip(folder, destination):
    """Zips the git-tracked files in `folder`, paths relative to it."""
    files = sorted(git("ls-files", "-z", "--", folder).split("\0"))
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        for path in filter(None, files):
            info = zipfile.ZipInfo(os.path.relpath(path, folder).replace(os.sep, "/"), ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(ROOT, path), "rb") as f:
                z.writestr(info, f.read())
    with open(destination, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def render_index(entries):
    """YAML for the index; strings are JSON-quoted, which YAML accepts."""
    lines = []
    for entry in entries:
        first = True
        for key in ("id", "name", "metadata", "version", "date", "path", "sha256", "requires"):
            if key not in entry:
                continue
            prefix = "- " if first else "  "
            first = False
            value = entry[key]
            if key == "metadata":
                lines.append(f"{prefix}metadata:")
                lines += [f"    {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in value.items()]
            elif key == "requires":
                lines.append(f"{prefix}requires:")
                lines += [f"    - {json.dumps(v)}" for v in value]
            else:
                lines.append(f"{prefix}{key}: {json.dumps(value, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines)


def build(kind, outdir):
    """Builds every package under ROOT/<kind>/ into outdir. Returns the entries."""
    os.makedirs(outdir, exist_ok=True)
    entries = []
    source = os.path.join(ROOT, kind)
    for name in sorted(os.listdir(source)):
        folder = os.path.join(kind, name)
        if not os.path.isfile(os.path.join(ROOT, folder, f"{name}.yml")):
            continue
        entry = package_info(folder, kind)
        entry["sha256"] = build_zip(folder, os.path.join(outdir, entry["path"]))
        entries.append(entry)
        print(f"{kind}: {entry['id']} {entry['version']}")
    with open(os.path.join(outdir, "index.yml"), "w", encoding="utf-8") as f:
        f.write(render_index(entries))
    return entries


def main(outdir="_site"):
    outdir = os.path.abspath(outdir)
    shutil.rmtree(outdir, ignore_errors=True)
    build("plugins", outdir)
    build("scrapers", os.path.join(outdir, "scrapers"))


if __name__ == "__main__":
    main(*sys.argv[1:2])
