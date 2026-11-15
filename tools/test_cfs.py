#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["regex"]
# ///
"""Run custom_format_tests from .build/pcd.db against an approximation of the arr matcher.

Release-title and release-group regexes use the `regex` module (variable-length
lookbehinds like .NET). Source, resolution and remux come from a simple title parser,
so only use this for title-driven formats. Usage: tools/test_cfs.py [CF name prefix ...]
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import regex

DB = Path(__file__).resolve().parent.parent / ".build" / "pcd.db"


def parse(title: str) -> dict:
    t = title.lower()
    res = next((r for r in ("2160p", "1080p", "720p", "576p", "480p") if r in t), None)
    remux = bool(regex.search(r"\bremux\b", t))
    if regex.search(r"\bweb[ ._-]?rip\b", t):
        src = "webrip"
    elif regex.search(r"\bweb([ ._-]?dl)?\b|webdl", t):
        src = "web_dl"
    elif regex.search(r"\b(blu[ ._-]?ray|bd(rip)?|uhd)\b", t):
        src = "bluray_raw" if remux else "bluray"
    elif regex.search(r"\bdvd", t):
        src = "dvd"
    elif regex.search(r"\bhdtv\b", t):
        src = "television"
    else:
        src = None
    group = title.rsplit("-", 1)[1] if "-" in title else ""
    return {"resolution": res, "source": src, "remux": remux, "group": group}


def load(db):
    pats = dict(db.execute("SELECT name, pattern FROM regular_expressions"))
    conds = defaultdict(list)
    q = """SELECT c.custom_format_name, c.name, c.type, c.arr_type, c.negate, c.required,
                  p.regular_expression_name, s.source, r.resolution, m.quality_modifier
           FROM custom_format_conditions c
           LEFT JOIN condition_patterns p ON p.custom_format_name=c.custom_format_name AND p.condition_name=c.name
           LEFT JOIN condition_sources s ON s.custom_format_name=c.custom_format_name AND s.condition_name=c.name
           LEFT JOIN condition_resolutions r ON r.custom_format_name=c.custom_format_name AND r.condition_name=c.name
           LEFT JOIN condition_quality_modifiers m ON m.custom_format_name=c.custom_format_name AND m.condition_name=c.name"""
    for row in db.execute(q):
        conds[row[0]].append(row[1:])
    return pats, conds


def matches(conds, pats, title, kind):
    info = parse(title)
    arr = "radarr" if kind == "movie" else "sonarr"
    groups = defaultdict(list)
    if not any(c[2] in ("all", arr) for c in conds):
        return False  # this format doesn't exist in the other app
    for name, ctype, arr_type, negate, required, rx, src, res, mod in conds:
        if arr_type not in ("all", arr):
            continue
        if ctype == "release_title":
            hit = bool(regex.search(pats[rx], title, regex.I))
        elif ctype == "release_group":
            hit = bool(regex.search(pats[rx], info["group"], regex.I))
        elif ctype == "source":
            hit = info["source"] == src
        elif ctype == "resolution":
            hit = info["resolution"] == res
        elif ctype == "quality_modifier":
            hit = info["remux"] if mod == "remux" else False
        else:
            return None  # language, indexer flags, release type: not modelled
        hit = hit != bool(negate)
        groups[ctype].append((hit, required))
    # the apps' rule: conditions are grouped by type; a group matches when no required condition fails and
    # at least one condition in it (required or not, a satisfied negation included) matches
    return all(not any(r and not h for h, r in v) and any(h for h, _ in v) for v in groups.values())


def main():
    db = sqlite3.connect(DB)
    pats, conds = load(db)
    prefixes = tuple(sys.argv[1:])
    fails = total = 0
    for cf, title, kind, should in db.execute("SELECT custom_format_name, title, type, should_match FROM custom_format_tests ORDER BY id"):
        if prefixes and not cf.startswith(prefixes):
            continue
        total += 1
        got = matches(conds[cf], pats, title, kind)
        if got is None:
            print(f"SKIP {cf!r}: uses conditions this runner can't evaluate")
            continue
        if got != bool(should):
            fails += 1
            print(f"FAIL {cf!r}: expected {'match' if should else 'no match'}: {title}")
    print(f"{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
