#!/usr/bin/env python3
"""Generate tweaks/*.sql: this fork's custom formats, profiles and media management.

Edit the tables below, run `python3 tools/gen_tweaks.py`, then `python3 tools/build_db.py`.
The generated SQL is committed; this script is the source of truth for it.

Scoring scale. Quality order decides first; scores rank releases of the same quality. The score still reads
as overall quality, highest first, so the apps' release lists and the delay bypass can use it:

    BAN            -999999  never grabbed (minimum score is -100000)
    bypass         +100000  skips the delay profile: Top Remux (movies), 2160p Tier 1 Group (TV)
    quality band   10000s   Remux-2160p 40000 > WEB-DL 2160p 30000 > Remux-1080p 20000 > WEB-DL 1080p 10000
    edition        -5000    Special Edition: theatrical cut is the default
    HDR present    +5000    2160p WEB-DL only: any HDR beats any SDR
    source tier    1000s    gist tier: SDR or HDR list, DV+HDR10 counts as HDR (T1 +2000 … bad -1000);
                            the UHD DV list only ranks Profile 5 in the DV profile
    HDR type       +100s    DV+HDR10 +200 > HDR10+ +100 > HDR10 0, within a tier
    IMAX           +100     triggers an upgrade (upgrade increment is 100)
    repack         0        Sonarr/Radarr's native preferAndUpgrade upgrades same-group repacks only
    tiebreakers    < 100    season pack 50, preferred (remux) group 10-40, gist position <= 30, labels 4,
                            group tier 1-3;
                            these only choose the first grab, never trigger an upgrade
    remux groups   +100s    Remux Tier 01 300 > 02 200 > 03 100
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tweaks"

BAN = -999999
MIN_SCORE = -100000
INCREMENT = 100

# ---------------------------------------------------------------------------
# Streaming services: (tag, spelled-out name used in file names, title regex)
# The regex must match both scene tags and our renamed files ("Disney+ WEBDL-1080p").
# Each is followed by a WEB source, which keeps titles like "Mad Max" or "Stan & Ollie" out.
# Ladder names put the quality title between them ("Amazon 2160p WEB-DL"), so a resolution may sit in between.
WEB_AFTER = r"(?=[ ._-]+((480|576|720|1080|2160)p[ ._-]+)?web[ ._-]?(dl|rip)?\b)"
SERVICES = {
    "MA": ("Movies Anywhere", r"(?<!dts[ ._-]?hd[ ._-]?)\b(ma|movies[ ._-]anywhere)\b"),
    "MS": ("Microsoft Store", r"\b(msft|ms|microsoft[ ._-]store)\b"),
    "PLAY": ("Google Play", r"\b(play|google[ ._-]play)\b"),
    "NF": ("Netflix", r"\b(nf|netflix)\b"),
    "AMZN": ("Amazon", r"\b(amzn|amazon)\b"),
    "iT": ("iTunes", r"\b(it|itunes)\b"),
    "ATVP": ("Apple TV+", r"\b(atvp|aptv|apple[ ._-]tv(\+|plus)?)(?=[ ._-])"),
    "PCOK": ("Peacock", r"\b(pcok|peacock)\b"),
    "STAN": ("Stan", r"\b(stan)\b"),
    "HULU": ("Hulu", r"\b(hulu)\b"),
    "HTSR": ("Hotstar", r"\b(htsr|hotstar)\b"),
    "ZEE5": ("ZEE5", r"\b(zee5|zee[ ._-]5)\b"),
    "PMTP": ("Paramount+", r"\b(pmtp|paramount[ ._-]?(\+|plus))(?=[ ._-])"),
    "SHO": ("Showtime", r"\b(sho|showtime)\b"),
    "DSNP": ("Disney+", r"\b(dsnp|dsny|strp|disney[ ._-]?(\+|plus)|star[ ._-]?(\+|plus))(?=[ ._-])"),
    "HMAX": ("HBO Max", r"\b(hmax|hbom|hbo[ ._-]?max)\b"),
    "MAX": ("Max", r"(?<!hbo[ ._-])\b(max)\b"),
    # Sony renamed Bravia Core to Sony Pictures Core in 2023; scene tags still say BCORE.
    "BCORE": ("Sony Pictures Core", r"\b(bcore|bravia[ ._-]core|sony[ ._-]pictures[ ._-]core|core)\b"),
    "CR": ("Crunchyroll", r"\b(cr|crunchyroll)\b"),
    "KNPY": ("Kanopy", r"\b(knpy|kanopy)\b"),
    "SKST": ("SkyShowtime", r"\b(skst|skyshowtime)\b"),
    "TUBI": ("Tubi", r"\b(tubi)\b"),
    "CRAV": ("Crave", r"\b(crav|crave)\b"),
    "iP": ("BBC iPlayer", r"\b(ip|iplayer|bbc[ ._-]iplayer)\b"),
    "ALL4": ("All 4", r"\b(all4|all[ ._-]4|channel[ ._-]4)\b"),
    "ROKU": ("Roku", r"\b(roku)\b"),
    "ITVX": ("ITVX", r"\b(itvx)\b"),
}

# vevv's WEB source cheatsheet (gist 1d7037bb556c17e977b26b9f8f175853), best first within a tier.
# Tier scores: the gist's "good" tier +2000, "mixed" +1000, "OK"/unlisted 0, "bad" -1000.
GOOD, MIXED, OK, BAD = 2000, 1000, 0, -1000
# Sony Pictures Core (BCORE): remux-like bitrates, so it beats every other WEB-DL and always triggers an upgrade.
# A 2160p remux still replaces it; to keep a BCORE file, unmonitor the title in Radarr/Sonarr.
NEAR_REMUX = 4000
GIST = {
    "UHD SDR": [(NEAR_REMUX, ["BCORE"]), (GOOD, ["MA", "MS", "PLAY"]), (MIXED, ["NF", "AMZN", "iT", "ATVP", "PCOK", "STAN"]),
                (OK, ["HULU"]), (BAD, ["HTSR", "PMTP", "SHO"])],
    "UHD HDR": [(NEAR_REMUX, ["BCORE"]), (GOOD, ["MA", "MS", "HMAX", "DSNP", "NF"]),
                (MIXED, ["iT", "ATVP", "MAX", "AMZN", "PCOK", "PMTP", "HULU", "STAN"]), (BAD, ["HTSR", "SHO"])],
    "UHD DV": [(GOOD, ["DSNP", "NF", "HMAX"]), (MIXED, ["AMZN", "MAX", "PMTP", "iT", "ATVP"]),
               (BAD, ["PCOK", "MA", "HTSR"])],
    "HD": [(GOOD, ["DSNP", "MA", "CR"]), (MIXED, ["NF", "MAX", "AMZN"]),
           (OK, ["HMAX", "MS", "iT", "ATVP", "STAN", "KNPY", "PMTP", "SKST", "PCOK"]),
           (BAD, ["HULU", "HTSR", "TUBI", "CRAV"])],
    "SD": [(GOOD, ["PMTP", "PLAY", "DSNP", "HMAX"]), (MIXED, ["NF", "MAX"]), (BAD, ["STAN", "iT", "AMZN"])],
}
POSITION_STEP, POSITION_MAX = 5, 30
UPSTREAM_NAMING = {"Hulu", "ITVX"}


def position_bonus(i: int, n: int) -> int:
    return min(POSITION_MAX, POSITION_STEP * (n - 1 - i))


# Regexes shared by several formats.
RX = {
    "sg-dv": r"\b(dv|dovi|dolby[ ._-]?v(ision)?)\b",
    "sg-hdr-present": r"\bHDR(\b|\d)|\b(PQ|HLG)\b|^(?=.*\bhulu\b)(?=.*\b(dv|dovi|dolby[ ._-]?v(ision)?)\b)",
    "sg-hdr10plus": r"\bHDR10(\+|P(lus)?)",
    "sg-lossless-audio": r"\b(TrueHD|DTS[ ._-]?HD[ ._-]?MA|DTS[ ._-]?X|DTS:X|LPCM|PCM|FLAC|Atmos)\b",
    "sg-remux": r"\bREMUX\b",
    "sg-cinephiles": r"^(CiNEPHiLES)$",
    # After the year, so titles like "Step Up 3D (2010)" don't count. Radarr 3D keeps release names
    # (no library_rename hook), so only scene spellings need to match.
    "sg-3d": r"(?<=\b[12]\d{3}\b).*(\b|_)(3d|bd3d|mvc|mv[ ._-]?hevc)(\b|_)",
    "sg-3d-sbs-ou": r"(?<=\b[12]\d{3}\b).*(\b|_)([fh]?sbs|h?ou|h?tab|(half|full)[ ._-]?(sbs|ou|tab))(\b|_)",
}

# Audio printed in file names, one {Custom Format:X} token each. TRaSH's audio formats are mutually
# exclusive; where we want a different spelling we add a copy of the TRaSH format under our name
# (Radarr's token can't contain ':' or '.', and TRaSH prints ATMOS in caps). (name, TRaSH source or None)
AUDIO = [("TrueHD Atmos", "TrueHD ATMOS"), ("TrueHD", None), ("DTS-X", "DTS X"), ("DTS-HD MA", None),
         ("LPCM", "PCM"), ("FLAC", None), ("DD+ Atmos", "DD+ ATMOS"), ("DD+", None), ("DD", None), ("DTS", None),
         ("AAC", None), ("Opus", None)]

# Technical rejections only. TRaSH's group-name bans (LQ, LQ (Release Title), Bad Dual Groups, Anime LQ Groups)
# score 0: the private trackers already police release quality (user's call, 2026-11-17). Lossy Remux scores 0
# too: it is mostly older discs that only ever had lossy audio (same day).
BANS_COMMON = ["BR-DISK", "BR-DISK (BTN)", "3D", "Upscaled", "Generated Dynamic HDR",
               "Line/Mic Dubbed", "AV1", "Sing-Along Versions", "Black and White Editions",
               "Extras", "x265 (no HDR/DV)", "Retags", "Obfuscated", "Open Matte",
               "Language: Not Original"]

LADDER_SCORES = {
    "Special Edition": -5000,
    "Repack/Proper": 0, "Repack2": 0, "Repack3": 0,  # repacks upgrade via propers_repacks=preferAndUpgrade (same group only)
    "IMAX": 100,  # also matches IMAX Enhanced (see custom_formats), which is not scored separately
    "Season Pack": 50,
    "Criterion Collection": 4, "Masters of Cinema": 4, "Vinegar Syndrome": 4, "Remaster": 4, "4K Remaster": 4,
    "WEB Tier 01": 3, "WEB Tier 02": 2, "WEB Tier 03": 1,
    "Anime Web Tier 01": 3, "Anime Web Tier 02": 3, "Anime Web Tier 03": 2, "Anime Web Tier 04": 2,
    "Anime Web Tier 05": 1, "Anime Web Tier 06": 1,
    "Remux Tier 01": 300, "Remux Tier 02": 200, "Remux Tier 03": 100,
    "Anime BD Tier 01": 300, "Anime BD Tier 02": 300, "Anime BD Tier 03": 200, "Anime BD Tier 04": 200,
    "Anime BD Tier 05": 100, "Anime BD Tier 06": 100, "Anime BD Tier 07": 100, "Anime BD Tier 08": 100,
    "HDR (2160p WEB)": 5000, "HDR: DV+HDR10": 200, "HDR: HDR10+": 100,
    "DVD Remux": 6000, "SD WEB-DL": 3000,
    "Band: Remux-2160p": 40000, "Band: WEB-DL 2160p": 30000, "Band: Remux-1080p": 20000, "Band: WEB-DL 1080p": 10000,
}
# Preferred groups, best first: (group, upstream release-group regex, score). Same-source WEB-DLs from these groups
# are usually the same stream, so this only picks a consistent group across seasons. It outranks gist position and
# group tiers, never HDR type or a better source. Kept small enough that a season pack (+50) from the top group
# beats an existing unknown-group file by under the upgrade increment (50 + 40 + 3 < 100), so it never re-grabs.
PREFERRED_GROUPS = [("FLUX", "FLUX", 40), ("Kitsune", "Kitsune (2)", 30), ("HONE", "HONE", 20), ("NTb", "NTb", 10)]
# Same for remuxes: a tiebreaker within a remux tier (CiNEPHiLES is also in Remux Tier 01), never an upgrade.
PREFERRED_REMUX_GROUPS = [("CiNEPHiLES", "sg-cinephiles", 40)]
PREFERRED = {"Preferred Group": PREFERRED_GROUPS, "Preferred Remux Group": PREFERRED_REMUX_GROUPS}
LADDER_SCORES |= {f"{kind}: {g}": sc for kind, groups in PREFERRED.items() for g, _, sc in groups}
# Scores at or above this skip the delay profiles (see media()).
# Groups moved from TRaSH's bans (or a higher tier) to the lowest tier: (format, condition) pairs to remove,
# then group -> arr the Remux/WEB Tier 03 condition applies to.
DEMOTE_FROM = [("LQ", "126811"), ("LQ (Release Title)", "SiCFoI + CM4v"), ("Remux Tier 02", "SiCFoI"),
               ("Generated Dynamic HDR", "Flights"), ("WEB Tier 02", "Flights"), ("Anime LQ Groups", "AREY")]
DEMOTED = {"126811": "all", "SiCFoI": "all", "Flights": "all", "AREY": "all"}

BYPASS = 100000
BYPASS_SCORES = {"Top Remux": BYPASS, "Tier 1 Group": BYPASS}
MOVIE_DELAY, TV_DELAY = 48 * 60, 60  # minutes

ALL_QUALITIES = ["Remux-2160p", "WEBDL-2160p", "Remux-1080p", "WEBDL-1080p", "WEBDL-720p", "DVD", "WEBDL-480p",
                 "Raw-HD", "BR-DISK", "Bluray-2160p", "HDTV-2160p", "WEBRip-2160p", "Bluray-1080p", "HDTV-1080p",
                 "WEBRip-1080p", "Bluray-720p", "HDTV-720p", "WEBRip-720p", "Bluray-576p", "Bluray-480p",
                 "WEBRip-480p", "HDTV-480p", "DVD-R", "SDTV", "DVDSCR", "REGIONAL", "TELECINE", "TELESYNC", "CAM",
                 "WORKPRINT", "Unknown"]


# ---------------------------------------------------------------------------
def q(s) -> str:
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"


class SQL:
    def __init__(self):
        self.lines, self.regexes = [], set()

    def add(self, s):
        self.lines.append(s)

    def regex(self, name, pattern, desc=None):
        if name not in self.regexes:
            self.regexes.add(name)
            self.add(f"INSERT INTO regular_expressions (name, pattern, description) VALUES ({q(name)}, {q(pattern)}, {q(desc)});")
        return name

    def cf(self, name, desc, rename=0):
        self.add(f"INSERT INTO custom_formats (name, description, include_in_rename) VALUES ({q(name)}, {q(desc)}, {rename});")

    def cond(self, cf, name, ctype, value, negate=0, required=0, arr="all"):
        self.add(f"INSERT INTO custom_format_conditions (custom_format_name, name, type, arr_type, negate, required) "
                 f"VALUES ({q(cf)}, {q(name)}, {q(ctype)}, {q(arr)}, {negate}, {required});")
        table, col = {"release_title": ("condition_patterns", "regular_expression_name"),
                      "release_group": ("condition_patterns", "regular_expression_name"),
                      "source": ("condition_sources", "source"),
                      "resolution": ("condition_resolutions", "resolution"),
                      "quality_modifier": ("condition_quality_modifiers", "quality_modifier")}[ctype]
        self.add(f"INSERT INTO {table} (custom_format_name, condition_name, {col}) VALUES ({q(cf)}, {q(name)}, {q(value)});")

    def title(self, cf, name, pattern_name, negate=0, required=1):
        self.cond(cf, name, "release_title", pattern_name, negate, required)

    def test(self, cf, title, should, kind="movie", desc=None):
        self.add(f"INSERT INTO custom_format_tests (custom_format_name, title, type, should_match, description) "
                 f"VALUES ({q(cf)}, {q(title)}, {q(kind)}, {should}, {q(desc)});")

    def write(self, path, header):
        path.write_text(f"-- {header}\n-- Generated by tools/gen_tweaks.py; edit that file, not this one.\n\n" + "\n".join(self.lines) + "\n")


def web_source(s, cf):
    s.cond(cf, "WEBDL", "source", "web_dl", required=1)


def custom_formats() -> SQL:
    s = SQL()
    for name, pat in RX.items():
        s.regex(name, pat)
    for tag, (spelled, pat) in SERVICES.items():
        s.regex(f"sg-svc-{tag}", pat + WEB_AFTER, f"{spelled} followed by a WEB source")

    # Naming-only service formats: print the spelled-out service before the quality.
    # Upstream already has naming-compatible "Hulu" and "ITVX" formats; those are reused.
    for tag, (spelled, _) in SERVICES.items():
        if spelled in UPSTREAM_NAMING:
            continue
        s.cf(spelled, f"Naming only (score 0): prints '{spelled}' for {tag} releases.", rename=1)
        s.title(spelled, spelled, f"sg-svc-{tag}")
        s.cond(spelled, "WEBDL", "source", "web_dl")
        s.cond(spelled, "WEBRIP", "source", "webrip")

    # Naming-only audio formats: copies of TRaSH audio formats under our spelling.
    for name, src in AUDIO:
        if src is None:
            continue
        s.cf(name, f"Naming only (score 0): copy of TRaSH's '{src}' that prints as '{name}'.")
        s.add("INSERT INTO custom_format_conditions (custom_format_name, name, type, arr_type, negate, required) "
              f"SELECT {q(name)}, name, type, arr_type, negate, required FROM custom_format_conditions WHERE custom_format_name = {q(src)};")
        s.add("INSERT INTO condition_patterns (custom_format_name, condition_name, regular_expression_name) "
              f"SELECT {q(name)}, condition_name, regular_expression_name FROM condition_patterns WHERE custom_format_name = {q(src)};")

    # HDR on 2160p WEB-DL.
    s.cf("HDR (2160p WEB)", "Any HDR layer on a 2160p WEB-DL, so every HDR release ranks above SDR.")
    s.title("HDR (2160p WEB)", "HDR", "sg-hdr-present")
    s.cond("HDR (2160p WEB)", "2160p", "resolution", "2160p", required=1)
    web_source(s, "HDR (2160p WEB)")
    s.cf("HDR: DV+HDR10", "Dolby Vision with an HDR10 base layer (Hulu's 'DV' is Profile 8).")
    s.title("HDR: DV+HDR10", "DV", "sg-dv")
    s.title("HDR: DV+HDR10", "HDR", "sg-hdr-present")
    s.cond("HDR: DV+HDR10", "2160p", "resolution", "2160p", required=1)
    web_source(s, "HDR: DV+HDR10")
    s.cf("HDR: HDR10+", "HDR10+ without Dolby Vision.")
    s.title("HDR: HDR10+", "HDR10+", "sg-hdr10plus")
    s.title("HDR: HDR10+", "Not DV", "sg-dv", negate=1)
    s.cond("HDR: HDR10+", "2160p", "resolution", "2160p", required=1)
    web_source(s, "HDR: HDR10+")

    # Gist source tiers, one format per (list, service). The HDR condition picks the list
    # for the release's HDR type; DV+HDR10 counts as HDR10. The UHD DV list only ranks
    # Profile 5 releases (the "Gist P5" formats below).
    for lst, tiers in GIST.items():
        if lst == "UHD DV":
            continue
        for tier_score, tags in tiers:
            for tag in tags:
                cf = f"Gist {lst}: {tag}"
                s.cf(cf, f"{SERVICES[tag][0]} on the gist's {lst} list.")
                s.title(cf, "Service", f"sg-svc-{tag}")
                web_source(s, cf)
                if lst.startswith("UHD"):
                    s.cond(cf, "2160p", "resolution", "2160p", required=1)
                if lst == "UHD HDR":
                    s.title(cf, "HDR", "sg-hdr-present")
                elif lst == "UHD SDR":
                    s.title(cf, "Not HDR", "sg-hdr-present", negate=1)
                    s.title(cf, "Not DV", "sg-dv", negate=1)
                elif lst == "HD":
                    s.cond(cf, "1080p", "resolution", "1080p")
                    s.cond(cf, "720p", "resolution", "720p")
                elif lst == "SD":
                    for r in ("2160p", "1080p", "720p"):
                        s.cond(cf, f"Not {r}", "resolution", r, negate=1, required=1)
        # Profile 5 library: the UHD DV tiers rank both its 2160p and 1080p rungs.
    for tier_score, tags in GIST["UHD DV"]:
        for tag in tags:
            cf = f"Gist P5: {tag}"
            s.cf(cf, f"{SERVICES[tag][0]} Profile 5 DV, ranked by the gist's UHD DV list.")
            s.title(cf, "Service", f"sg-svc-{tag}")
            s.title(cf, "DV", "sg-dv")
            s.title(cf, "Not HDR", "sg-hdr-present", negate=1)
            web_source(s, cf)

    # Remuxes.
    s.cf("Lossy Remux", "Remux without lossless audio (TrueHD, DTS-HD MA, DTS:X, LPCM or FLAC). Not scored.")
    s.cond("Lossy Remux", "Remux", "quality_modifier", "remux", required=1, arr="radarr")
    s.cond("Lossy Remux", "Remux Source", "source", "bluray_raw", required=1, arr="sonarr")
    s.title("Lossy Remux", "Not lossless", "sg-lossless-audio", negate=1)
    s.cond("Lossy Remux", "Not DVD", "source", "dvd", negate=1, required=1)
    s.cf("DVD Remux", "Untouched DVD video. Same quality as a DVD encode in the apps, so ranked by score.")
    s.cond("DVD Remux", "DVD", "source", "dvd", required=1)
    s.title("DVD Remux", "Remux", "sg-remux")
    s.cf("3D Remux", "Frame-packed (MVC) Blu-ray 3D remux: 3D in the title, a remux, and not SBS/OU. "
         "Only the 3D profile scores it; MVC remuxes are often labelled AVC, so it doesn't require 'MVC'.")
    s.title("3D Remux", "3D", "sg-3d")
    s.title("3D Remux", "Not SBS/OU", "sg-3d-sbs-ou", negate=1)
    s.cond("3D Remux", "Remux", "quality_modifier", "remux", required=1, arr="radarr")
    s.cf("3D WEB-DL", "Frame-packed (MV-HEVC) 3D WEB-DL, e.g. Disney+'s 3D streams: 3D in the title, a WEB-DL, "
         "and not SBS/OU. Only the 3D profile scores it.")
    s.title("3D WEB-DL", "3D", "sg-3d")
    s.title("3D WEB-DL", "Not SBS/OU", "sg-3d-sbs-ou", negate=1)
    web_source(s, "3D WEB-DL")
    s.cf("SD WEB-DL", "480p/576p WEB-DL, ranked between DVD Remux and DVD encodes.")
    web_source(s, "SD WEB-DL")
    for r in ("2160p", "1080p", "720p"):
        s.cond("SD WEB-DL", f"Not {r}", "resolution", r, negate=1, required=1)

    # Quality bands: one format per enabled rung, so the score orders releases across qualities too.
    for res in ("2160p", "1080p"):
        cf = f"Band: Remux-{res}"
        s.cf(cf, f"Quality band for {res} remuxes (scoring only).")
        s.cond(cf, "Remux", "quality_modifier", "remux", required=1, arr="radarr")
        s.cond(cf, "Remux Source", "source", "bluray_raw", required=1, arr="sonarr")
        s.cond(cf, res, "resolution", res, required=1)
        cf = f"Band: WEB-DL {res}"
        s.cf(cf, f"Quality band for {res} WEB-DLs (scoring only).")
        web_source(s, cf)
        s.cond(cf, res, "resolution", res, required=1)

    # Delay bypass formats. Their groups are copied from TRaSH's tier formats, so they follow upstream.
    def copy_groups(cf, src, arr):
        s.add("INSERT INTO custom_format_conditions (custom_format_name, name, type, arr_type, negate, required) "
              f"SELECT {q(cf)}, name, type, {q(arr)}, 0, 0 FROM custom_format_conditions WHERE custom_format_name = {q(src)} "
              f"AND type = 'release_group' AND arr_type IN ('all', {q(arr)}) AND name NOT IN "
              f"(SELECT name FROM custom_format_conditions WHERE custom_format_name = {q(cf)});")
        s.add("INSERT INTO condition_patterns (custom_format_name, condition_name, regular_expression_name) "
              f"SELECT {q(cf)}, p.condition_name, p.regular_expression_name FROM condition_patterns p "
              f"JOIN custom_format_conditions c ON c.custom_format_name = p.custom_format_name AND c.name = p.condition_name "
              f"WHERE p.custom_format_name = {q(src)} AND c.type = 'release_group' AND c.arr_type IN ('all', {q(arr)}) "
              f"AND p.condition_name NOT IN (SELECT condition_name FROM condition_patterns WHERE custom_format_name = {q(cf)});")
    s.cf("Top Remux", "Movies: 2160p remux from a Remux Tier 01 group with Dolby Vision and HDR10+. Skips the 48-hour delay.")
    s.cond("Top Remux", "Remux", "quality_modifier", "remux", required=1, arr="radarr")
    s.cond("Top Remux", "2160p", "resolution", "2160p", required=1, arr="radarr")
    s.cond("Top Remux", "DV", "release_title", "sg-dv", required=1, arr="radarr")
    s.cond("Top Remux", "HDR10+", "release_title", "sg-hdr10plus", required=1, arr="radarr")
    copy_groups("Top Remux", "Remux Tier 01", "radarr")
    # 2160p only: a tier-1 1080p waits, so a 2160p from the same group posted shortly after is grabbed instead of both.
    s.cf("Tier 1 Group", "TV: a 2160p release from a WEB Tier 01 or Remux Tier 01 group. Skips the 60-minute delay.")
    s.cond("Tier 1 Group", "2160p", "resolution", "2160p", required=1, arr="sonarr")
    copy_groups("Tier 1 Group", "WEB Tier 01", "sonarr")
    copy_groups("Tier 1 Group", "Remux Tier 01", "sonarr")

    # Preferred groups: one format each, so each can carry its own score (see PREFERRED_GROUPS).
    for kind, groups in PREFERRED.items():
        for g, rx, _ in groups:
            cf = f"{kind}: {g}"
            s.cf(cf, f"Release group {g}. A tiebreaker between otherwise equal releases ({kind.lower()}s, best first: "
                 + " > ".join(p for p, _, _ in groups) + ").")
            s.cond(cf, g, "release_group", rx, required=1)

    # Demoted groups: TRaSH bans these for naming or generated-metadata problems; here they rank as the
    # lowest tier instead, so their releases are still grabbed when nothing better exists.
    for cf, cond in DEMOTE_FROM:
        s.add(f"DELETE FROM custom_format_conditions WHERE custom_format_name = {q(cf)} AND name = {q(cond)};")
    for g, arr in DEMOTED.items():
        rx = s.regex(f"sg-grp-{g}", f"^({g})$", f"Release group {g}")
        for cf in ("Remux Tier 03", "WEB Tier 03"):
            s.cond(cf, g, "release_group", rx, arr=arr)

    # The library names a remux of a 3D disc's 2D video "<label> Blu-ray 3D Remux"; release names spell 3D
    # discs BluRay.3D/BD3D/3D.HSBS, so this exact spelling is exempt from the 3D ban.
    # (A required negated condition can't do this: the apps count a satisfied negation as the title group's
    # match, so the format would match every release. The exemption goes into the 3D pattern instead.)
    s.add("UPDATE regular_expressions SET pattern = "
          + q(r"(?<=\b[12]\d{3}\b).*(\b|_)(?<!Blu-ray )(3d|[fh]?sbs|half[ ._-]ou)(\b|_)")
          + " WHERE name = '3D' AND pattern = " + q(r"(?<=\b[12]\d{3}\b).*(\b|_)(3d|[fh]?sbs|half[ ._-]ou)(\b|_)") + ";")

    # IMAX covers IMAX Enhanced too, so both print and score as plain IMAX.
    s.add("DELETE FROM custom_format_conditions WHERE custom_format_name = 'IMAX' AND name = 'NOT: IMAX Enhanced';")
    # Only spelled-out services, IMAX and the audio formats print; everything else is off.
    # Audio formats must be included too: {Custom Format:X} prints nothing otherwise. They are kept out
    # of the service list by the exclusion filter in the naming format. IMAX prints here as a technical
    # tag; the media stack's import hook strips it from Radarr's edition.
    printed = [spelled for spelled, _ in SERVICES.values()] + ["IMAX"] + [n for n, _ in AUDIO]
    s.add("UPDATE custom_formats SET include_in_rename = 0;")
    s.add(f"UPDATE custom_formats SET include_in_rename = 1 WHERE name IN ({', '.join(q(n) for n in printed)});")
    return s


def tests() -> SQL:
    s = SQL()
    m, t = "movie", "series"
    for cf, title, should, kind in [
        ("Movies Anywhere", "Camp.Miasma.2023.1080p.WEB-DL.DDP5.1.H.264-GRP", 0, m),
        ("3D", "Dial M for Murder (1954) {imdb-tt0046912} Warner Bros. (2012) Blu-ray 3D Remux [1080p 1.78 FLAC 1.0]-KRaLiMaRKo", 0, m),
        ("3D", "Dial.M.for.Murder.1954.1080p.BluRay.3D.REMUX.AVC.FLAC.1.0-KRaLiMaRKo", 1, m),
        ("3D", "Avatar.2009.1080p.3D.BluRay.Half-SBS.x264-GRP", 1, m),
        ("3D", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 0, m),
        ("3D", "Dial M for Murder (1954) Warner Bros. (2012) Blu-ray 3D Remux [1080p 1.78 FLAC 1.0] Half-SBS", 1, m),
        ("3D", "Avatar.2009.BD3D.1080p.REMUX.MVC.DTS-HD.MA.5.1-GRP", 1, m),
        ("3D Remux", "Spider-Man.Into.the.Spider.Verse.2018.3D.1080p.BluRay.REMUX.AVC.Atmos-EPSiLON", 1, m),
        ("3D Remux", "Dial.M.for.Murder.1954.1080p.BluRay.3D.REMUX.AVC.FLAC.1.0-KRaLiMaRKo", 1, m),
        ("3D Remux", "Avatar.2009.BD3D.1080p.REMUX.MVC.DTS-HD.MA.5.1-GRP", 1, m),
        ("3D Remux", "Avatar.2009.1080p.3D.BluRay.Half-SBS.x264-GRP", 0, m),
        ("3D Remux", "Avatar.2009.1080p.3D.BluRay.REMUX.Half-OU.DTS-HD.MA.5.1-GRP", 0, m),
        ("3D Remux", "Avatar.2009.1080p.BluRay.3D.FSBS.REMUX.DTS-HD.MA.5.1-GRP", 0, m),
        ("3D Remux", "Avatar.2009.1080p.3D.BluRay.x264.DTS-HD.MA.5.1-GRP", 0, m),
        ("3D Remux", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 0, m),
        ("3D Remux", "Step.Up.3D.2010.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-GRP", 0, m),
        ("3D Remux", "The.Mandalorian.and.Grogu.2026.2160p.3D.DSNP.WEB-DL.DDP.5.1.Atmos.DV.H.265-AXON", 0, m),
        ("3D WEB-DL", "The.Mandalorian.and.Grogu.2026.2160p.3D.DSNP.WEB-DL.DDP.5.1.Atmos.DV.H.265-AXON", 1, m),
        ("3D WEB-DL", "The Mandalorian and Grogu 2026 2160p 3D DSNP WEB-DL DDP 5.1 Atmos DV H.265-AXON", 1, m),
        ("3D WEB-DL", "Some.Film.2026.1080p.DSNP.WEB-DL.DDP5.1.MV-HEVC-GRP", 1, m),
        ("3D WEB-DL", "Some.Film.2026.1080p.3D.DSNP.WEB-DL.Half-SBS.DDP5.1.H.264-GRP", 0, m),
        ("3D WEB-DL", "Some.Film.2026.2160p.DSNP.WEB-DL.DDP5.1.Atmos.DV.H.265-GRP", 0, m),
        ("3D WEB-DL", "Spider-Man.Into.the.Spider.Verse.2018.3D.1080p.BluRay.REMUX.AVC.Atmos-EPSiLON", 0, m),
        ("3D WEB-DL", "Step.Up.3D.2010.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP", 0, m),
        ("3D", "The.Mandalorian.and.Grogu.2026.2160p.3D.DSNP.WEB-DL.DDP.5.1.Atmos.DV.H.265-AXON", 1, m),
        ("LQ (Release Title)", "Saw IV (2007) Blu-ray Remux [4K UHD 1.85 DV P7 FEL CMv4.0 HDR10+ TrueHD Atmos 7.1]-SiCFoI", 0, m),
        ("Remux Tier 03", "Saw.IV.2007.2160p.UHD.BluRay.REMUX.DV.HDR10P.HEVC.TrueHD.7.1.Atmos-SiCFoI", 1, m),
        ("Remux Tier 02", "Saw.IV.2007.2160p.UHD.BluRay.REMUX.DV.HDR10P.HEVC.TrueHD.7.1.Atmos-SiCFoI", 0, m),
        ("LQ", "Ne.Zha.2019.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-126811", 0, m),
        ("Remux Tier 03", "Ne.Zha.2019.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-126811", 1, m),
        ("Generated Dynamic HDR", "Shrek.2.2004.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-X.7.1-Flights", 0, m),
        ("Remux Tier 03", "Shrek.2.2004.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-X.7.1-Flights", 1, m),
        ("WEB Tier 03", "Bad.Boys.for.Life.2020.2160p.CORE.WEB-DL.DTS-X.7.1.DV.HDR.H.265-Flights", 1, m),
        ("Anime LQ Groups", "Formula.1.Drive.to.Survive.S01E01.2160p.NF.WEB-DL.DDP5.1.Atmos.H.265-AREY", 0, t),
        ("WEB Tier 03", "Formula.1.Drive.to.Survive.S01E01.2160p.NF.WEB-DL.DDP5.1.Atmos.H.265-AREY", 1, t),
        ("Movies Anywhere", "Cast.Away.2000.2160p.MA.WEB-DL.DTS-HD.MA.5.1.DV.HDR10P.H.265-XEBEC", 1, m),
        ("Movies Anywhere", "Cast Away (2000) {imdb-tt0162222} Movies Anywhere WEBDL-2160p DV HDR10Plus DTS-HD MA 5.1-XEBEC", 1, m),
        ("Movies Anywhere", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 0, m),
        ("iTunes", "Some.Film.2010.1080p.iT.WEB-DL.DD5.1.H.264-GRP", 1, m),
        ("iTunes", "Some Film (2010) {imdb-tt1} iTunes WEBDL-1080p DD 5.1-GRP", 1, m),
        ("Max", "Mad.Max.Fury.Road.2015.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-GRP", 0, m),
        ("Max", "Mad Max Fury Road (2015) {imdb-tt1392190} WEBDL-1080p DD+ 5.1-GRP", 0, m),
        ("HBO Max", "Mad.Max.Fury.Road.2015.1080p.HMAX.WEB-DL.DD5.1.H.264-GRP", 1, m),
        ("Stan", "Stan.and.Ollie.2018.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP", 0, m),
        ("Stan", "Some.Show.S01E01.1080p.STAN.WEB-DL.AAC2.0.H.264-GRP", 1, t),
        ("ZEE5", "Some.Film.2010.1080p.ZEE5.WEB-DL.DD5.1.H.264-GRP", 1, m),
        ("ZEE5", "Some Film (2010) {imdb-tt1} ZEE5 WEBDL-1080p DD 5.1-GRP", 1, m),
        ("BBC iPlayer", "Some.Show.S01E01.1080p.iP.WEB-DL.AAC2.0.H.264-GRP", 1, t),
        ("Disney+", "Idiocracy (2006) {imdb-tt0387808} Disney+ WEBDL-1080p DD+ 5.1-FLUX", 1, m),
        ("Disney+", "Idiocracy (2006) {imdb-tt0387808} Disney+ 1080p WEB-DL EAC3 5.1-FLUX", 1, m),
        ("Amazon", "Top Gear (2002) - S25E02 - Episode 2 - Amazon 1080p WEB-DL EAC3 2.0", 1, t),
        ("Netflix", "Some Show (2020) - S01E01 - Pilot - Netflix 2160p WEB-DL DV HDR10 EAC3 Atmos 5.1-GRP", 1, t),
        ("Max", "Mad Max (1979) {imdb-tt0079501} 1080p Blu-ray Remux DTS-HD MA 2.0", 0, m),
        ("Apple TV+", "Some.Show.S01E01.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, t),
        ("Paramount+", "Some.Show.S01E01.1080p.PMTP.WEB-DL.DDP5.1.H.264-GRP", 1, t),
        ("Sony Pictures Core", "Some.Film.2010.2160p.BCORE.WEB-DL.DTS-HD.MA.5.1.HDR.H.265-GRP", 1, m),
        ("Sony Pictures Core", "Call Me by Your Name (2017) {imdb-tt5726616} 4K HDR10 Sony Pictures Core WEB-DL DTS-HD MA", 1, m),
        ("Sony Pictures Core", "Men in Black (1997) {imdb-tt0119654} Sony Pictures Core 2160p WEB-DL HDR10 DTS-HD MA 7.1", 1, m),
        ("IMAX", "Dune.2021.IMAX.Enhanced.2160p.BCORE.WEB-DL.DTS-X.7.1.HDR.H.265-GRP", 1, m),
        ("IMAX", "Dune (2021) {imdb-tt1160419} Disney+ IMAX WEBDL-2160p HDR10 DD+ Atmos 5.1-GRP", 1, m),
        ("Lossy Remux", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 0, m),
        ("Lossy Remux", "Heat.1995.1080p.BluRay.REMUX.AVC.DD.5.1-GRP", 1, m),
        ("Lossy Remux", "Heat.1995.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-GRP", 0, m),
        ("DVD Remux", "Heat.1995.NTSC.DVD.REMUX.MPEG-2.DD.5.1-GRP", 1, m),
        ("Lossy Remux", "Heat.1995.NTSC.DVD.REMUX.MPEG-2.DD.5.1-GRP", 0, m),
        ("DVD Remux", "Heat.1995.DVDRip.x264.AC3-GRP", 0, m),
        ("HDR: DV+HDR10", "Some.Film.2010.2160p.DSNP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, m),
        ("HDR: DV+HDR10", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-GRP", 0, m),
        ("HDR: DV+HDR10", "The.Bear.S01E01.2160p.HULU.WEB-DL.DDP5.1.DV.H.265-GRP", 1, t),
        ("HDR: HDR10+", "Some.Film.2010.2160p.AMZN.WEB-DL.DDP5.1.HDR10+.H.265-GRP", 1, m),
        ("Gist UHD HDR: MA", "Some.Film.2010.2160p.MA.WEB-DL.DDP5.1.Atmos.HDR.H.265-GRP", 1, m),
        ("Gist UHD HDR: MA", "Some.Film.2010.2160p.MA.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, m),
        ("Gist UHD HDR: HULU", "The.Bear.S01E01.2160p.HULU.WEB-DL.DDP5.1.DV.H.265-GRP", 1, t),
        ("Gist UHD HDR: NF", "Fallout.S02E01.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-GRP", 0, t),
        ("Gist UHD SDR: MA", "Some.Film.2010.2160p.MA.WEB-DL.DDP5.1.H.265-GRP", 1, m),
        ("Gist HD: DSNP", "Some.Film.2010.1080p.DSNP.WEB-DL.DDP5.1.H.264-GRP", 1, m),
        ("Gist P5: NF", "Fallout.S02E01.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-GRP", 1, t),
        ("Gist P5: NF", "Fallout.S02E01.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 0, t),
        ("DD+ Atmos", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, m),
        ("DD+ Atmos", "Some Film (2010) {imdb-tt1} Netflix WEBDL-2160p DV HDR10 DD+ Atmos 5.1-GRP", 1, m),
        ("DD+", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 0, m),
        ("TrueHD Atmos", "Heat.1995.2160p.BluRay.REMUX.HEVC.TrueHD.Atmos.7.1-GRP", 1, m),
        ("TrueHD", "Heat.1995.2160p.BluRay.REMUX.HEVC.TrueHD.Atmos.7.1-GRP", 0, m),
        ("DTS-HD MA", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 1, m),
        ("DTS-X", "Heat.1995.2160p.BluRay.REMUX.HEVC.DTS-X.7.1-GRP", 1, m),
        ("DTS-X", "Heat (1995) {imdb-tt0113277} Remux-2160p HDR10 DTS-X 7.1-GRP", 1, m),
        ("DTS-HD MA", "Heat.1995.2160p.BluRay.REMUX.HEVC.DTS-X.7.1-GRP", 0, m),
        ("LPCM", "Heat.1995.1080p.BluRay.REMUX.AVC.LPCM.2.0-GRP", 1, m),
        ("Top Remux", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-FraMeSToR", 1, m),
        ("Top Remux", "Dune (2021) {imdb-tt1160419} Remux-2160p DV HDR10Plus TrueHD Atmos 7.1-CiNEPHiLES", 1, m),
        ("Top Remux", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-FraMeSToR", 0, m),
        ("Top Remux", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-GRP", 0, m),
        ("Top Remux", "Dune.2021.1080p.BluRay.REMUX.AVC.TrueHD.Atmos.7.1-FraMeSToR", 0, m),
        ("Top Remux", "Dune.2021.2160p.AMZN.WEB-DL.DDP5.1.Atmos.DV.HDR10+.H.265-FLUX", 0, m),
        ("Tier 1 Group", "Some.Show.S01E01.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-FLUX", 1, t),
        ("Tier 1 Group", "Some.Show.S01E01.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-FraMeSToR", 1, t),
        ("Tier 1 Group", "Some.Show.S01E01.1080p.NF.WEB-DL.DDP5.1.H.264-FLUX", 0, t),
        ("Tier 1 Group", "Some.Show.S01E01.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FraMeSToR", 0, t),
        ("Tier 1 Group", "Some.Show.S01E01.2160p.NF.WEB-DL.DDP5.1.H.265-GRP", 0, t),
        ("Tier 1 Group", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.H.265-FLUX", 0, m),
        ("Tier 1 Group", "Percy.Jackson.and.the.Olympians.S03E01.2160p.DSNP.WEB-DL.DDP5.1.Atmos.DV.H.265-Kitsune", 1, t),
        ("Band: Remux-2160p", "Heat.1995.2160p.UHD.BluRay.REMUX.HEVC.TrueHD.Atmos-GRP", 1, m),
        ("Band: Remux-2160p", "Heat.S01E01.2160p.UHD.BluRay.REMUX.HEVC.TrueHD.Atmos-GRP", 1, t),
        ("Band: Remux-2160p", "Heat.1995.2160p.AMZN.WEB-DL.DDP5.1.H.265-GRP", 0, m),
        ("Band: WEB-DL 2160p", "Heat.1995.2160p.AMZN.WEB-DL.DDP5.1.H.265-GRP", 1, m),
        ("Band: Remux-1080p", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 1, m),
        ("Band: WEB-DL 1080p", "Heat.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP", 1, t),
        ("Band: WEB-DL 1080p", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 0, m),
        ("Preferred Group: FLUX", "The.Diplomat.S04.2160p.NF.WEB-DL.DDP5.1.Atmos.DoVi.HDR.H.265-FLUX", 1, t),
        ("Preferred Group: FLUX", "The Diplomat S04 2160p NF WEB-DL DD+ 5.1 Atmos DV HDR H.265-Kitsune", 0, t),
        ("Preferred Group: Kitsune", "The Diplomat S04 2160p NF WEB-DL DD+ 5.1 Atmos DV HDR H.265-Kitsune", 1, t),
        ("Preferred Group: HONE", "The.Diplomat.S03.Hybrid.2160p.NF.WEB-DL.DDP5.1.Atmos.DoVi.HDR.H.265-HONE", 1, t),
        ("Preferred Group: HONE", "The Diplomat S02 2160p NF WEB-DL DD+ 5.1 Atmos DV HDR H.265-SiC", 0, t),
        ("Preferred Group: NTb", "Some.Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb", 1, t),
        ("Preferred Group: NTb", "Some.Film.2010.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb", 1, m),
        ("Preferred Remux Group: CiNEPHiLES", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES", 1, m),
        ("Preferred Remux Group: CiNEPHiLES", "Dune (2021) {imdb-tt1160419} Remux-2160p DV HDR10Plus TrueHD Atmos 7.1-CiNEPHiLES", 1, m),
        ("Preferred Remux Group: CiNEPHiLES", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-FraMeSToR", 0, m),
        ("Remux Tier 01", "Dune.2021.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES", 1, m),
    ]:
        s.test(cf, title, should, kind)
    return s


def ladder_profile(s: SQL, name: str, desc: str, rungs: list, top: str, min_score: int, until_score: int, scores: dict, bans: list):
    s.add(f"INSERT INTO quality_profiles (name, description, upgrades_allowed, minimum_custom_format_score, upgrade_until_score, upgrade_score_increment) "
          f"VALUES ({q(name)}, {q(desc)}, 1, {min_score}, {until_score}, {INCREMENT});")
    pos, enabled = 0, set()
    for r in rungs:
        if isinstance(r, tuple):  # quality group
            gname, members = r
            s.add(f"INSERT INTO quality_groups (quality_profile_name, name) VALUES ({q(name)}, {q(gname)});")
            for i, m in enumerate(members):
                s.add(f"INSERT INTO quality_group_members (quality_profile_name, quality_group_name, quality_name, position) VALUES ({q(name)}, {q(gname)}, {q(m)}, {i});")
                enabled.add(m)
            s.add(f"INSERT INTO quality_profile_qualities (quality_profile_name, quality_group_name, position, enabled, upgrade_until) VALUES ({q(name)}, {q(gname)}, {pos}, 1, {int(gname == top)});")
        else:
            enabled.add(r)
            s.add(f"INSERT INTO quality_profile_qualities (quality_profile_name, quality_name, position, enabled, upgrade_until) VALUES ({q(name)}, {q(r)}, {pos}, 1, {int(r == top)});")
        pos += 1
    for qn in ALL_QUALITIES:
        if qn not in enabled:
            s.add(f"INSERT INTO quality_profile_qualities (quality_profile_name, quality_name, position, enabled, upgrade_until) VALUES ({q(name)}, {q(qn)}, {pos}, 0, 0);")
            pos += 1
    s.add(f"INSERT INTO quality_profile_languages (quality_profile_name, language_name, type) VALUES ({q(name)}, 'Original', 'simple');")
    for cf in bans:
        scores[cf] = BAN
    for cf, sc in scores.items():
        s.add(f"INSERT INTO quality_profile_custom_formats (quality_profile_name, custom_format_name, arr_type, score) VALUES ({q(name)}, {q(cf)}, 'all', {sc});")
    # Formats whose conditions all belong to one app (e.g. Season Pack) are only scored there;
    # the other app rejects a format with no conditions.
    s.add("UPDATE quality_profile_custom_formats SET arr_type = (SELECT min(c.arr_type) FROM custom_format_conditions c "
          "WHERE c.custom_format_name = quality_profile_custom_formats.custom_format_name) "
          f"WHERE quality_profile_name = {q(name)} AND custom_format_name IN (SELECT custom_format_name FROM custom_format_conditions "
          "GROUP BY custom_format_name HAVING count(DISTINCT arr_type) = 1 AND min(arr_type) IN ('radarr', 'sonarr'));")


def profiles() -> SQL:
    s = SQL()
    naming = {spelled: 0 for spelled, _ in SERVICES.values()} | {n: 0 for n, _ in AUDIO} | {"IMAX": 0}

    scores = dict(LADDER_SCORES) | BYPASS_SCORES
    for lst, tiers in GIST.items():
        if lst == "UHD DV":
            continue
        for tier_score, tags in tiers:
            for i, tag in enumerate(tags):
                scores[f"Gist {lst}: {tag}"] = tier_score + position_bonus(i, len(tags))
    ladder_profile(
        s, "Best Available",
        "One ladder for movies and TV: 2160p Remux (final) > 2160p WEB-DL > 1080p Remux > 1080p WEB-DL. "
        "720p and SD are never grabbed automatically; grab them by hand from Interactive Search. WEB-DLs rank by "
        "vevv's source gist for their own HDR type, then HDR type, then group. Remuxes need lossless audio and "
        "rank by TRaSH tier, then preferred group (CiNEPHiLES). Scores read as overall quality (bands of 10000 per rung); 100000+ skips the delay.",
        # 1080p WEB-DL is the lowest automatic grab. The 720p/SD scores (SD WEB-DL, DVD Remux, gist SD list)
        # stay defined for manual grabs and in case these rungs come back.
        ["Remux-2160p", "WEBDL-2160p", "Remux-1080p", "WEBDL-1080p"],
        top="Remux-2160p", min_score=MIN_SCORE, until_score=MIN_SCORE, scores=naming | scores,
        bans=BANS_COMMON + ["DV (w/o HDR fallback)"])

    p5 = {k: v for k, v in LADDER_SCORES.items() if k.startswith(("Repack", "WEB Tier", "Anime Web", "Season Pack", "Preferred Group"))}
    p5["DV (w/o HDR fallback)"] = 50000
    # Profile 5 alone tops out near 52000, under BYPASS: a 2160p Tier 1 Group release skips the delay here too.
    p5["Tier 1 Group"] = BYPASS
    for tier_score, tags in GIST["UHD DV"]:
        for i, tag in enumerate(tags):
            p5[f"Gist P5: {tag}"] = tier_score + position_bonus(i, len(tags))
    ladder_profile(
        s, "DV Profile 5",
        "Profile 5 Dolby Vision WEB-DLs only, for the DV libraries (RadarrDV, SonarrDV): 2160p > 1080p, "
        "both ranked by the gist's UHD DV tiers.",
        ["WEBDL-2160p", "WEBDL-1080p"], top="WEBDL-2160p", min_score=40000, until_score=999999, scores=naming | p5,
        bans=BANS_COMMON)

    # 3D library (Radarr 3D): frame-packed 3D only, for a 3D-capable player; Plex can't play it. A 2160p
    # MV-HEVC WEB-DL (Disney+ 3D, HDR/DV) is final and beats a 1080p MVC Blu-ray 3D remux; a 1080p 3D WEB-DL is last.
    # Radarr has no 3D quality, so the 3D Remux and 3D WEB-DL formats gate the grab.
    three_d = {k: v for k, v in LADDER_SCORES.items()
               if k.startswith(("Repack", "Remux Tier", "Preferred Remux Group", "Criterion", "Masters", "Vinegar", "Remaster",
                                "WEB Tier"))}
    three_d["3D Remux"] = 40000
    three_d["3D WEB-DL"] = 40000
    ladder_profile(
        s, "3D",
        "Frame-packed 3D only, for the 3D library (Radarr 3D): 2160p 3D WEB-DL (MV-HEVC, e.g. Disney+; final) "
        "> Blu-ray 3D remux (MVC, 1080p) > 1080p 3D WEB-DL. SBS/OU encodes, 2D releases and ISOs are never grabbed. "
        "Remuxes need lossless audio and rank by TRaSH tier, then preferred group (CiNEPHiLES).",
        ["WEBDL-2160p", "Remux-1080p", "WEBDL-1080p"], top="WEBDL-2160p", min_score=40000, until_score=999999,
        scores=naming | three_d,
        # MV-HEVC is always HEVC, so TRaSH's x265 ban would reject every SDR 1080p 3D WEB-DL.
        bans=[b for b in BANS_COMMON if b not in ("3D", "x265 (no HDR/DV)")])
    return s


def media() -> SQL:
    s = SQL()
    # rename = 0: media_utils' library_rename script owns file and folder names (decided 2026-11-14) and renames each
    # import from a webhook. The formats below only name a file between import and that rename, and name new folders.
    # Audio prints from the audio custom formats in our spelling (DD+, LPCM, ...). They only match release names
    # and filenames, so library files imported from disk had their media-info codec written into the filename
    # once (2026-11-14) so the formats match them too.
    details = "{Custom Formats:-" + ",".join(n for n, _ in AUDIO) + "} {Quality Full}{ MediaInfo VideoDynamicRangeType}" + "".join(
        "{ Custom Format:" + n + "}" for n, _ in AUDIO) + "{ MediaInfo AudioChannels}{-Release Group}"
    movie_file = "{Movie CleanTitle} ({Release Year}) {imdb-{ImdbId}} {edition-{Edition Tags}} " + details
    s.add("INSERT INTO radarr_naming (name, rename, movie_format, movie_folder_format, replace_illegal_characters, colon_replacement_format) "
          f"VALUES ('Best Available', 0, {q(movie_file)}, {q('{Movie CleanTitle} ({Release Year}) {imdb-{ImdbId}}')}, 1, 'smart');")
    series = "{Series CleanTitleWithoutYear} ({Series Year})"
    s.add("INSERT INTO sonarr_naming (name, rename, standard_episode_format, daily_episode_format, anime_episode_format, "
          "series_folder_format, season_folder_format, replace_illegal_characters, colon_replacement_format, multi_episode_style) VALUES ("
          f"'Best Available', 0, {q(series + ' - S{season:00}E{episode:00} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' - {Air-Date} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' - S01E{absolute:0000} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' {tvdb-{TvdbId}}')}, {q('Season {season:00}')}, 1, 4, 0);")
    for app in ("radarr", "sonarr"):
        s.add(f"INSERT INTO {app}_media_settings (name, propers_repacks, enable_media_info) VALUES ('Best Available', 'preferAndUpgrade', 1);")
        pref = 1999 if app == "radarr" else 995
        for qn in ALL_QUALITIES:
            if qn in ("Raw-HD", "BR-DISK") or (app == "sonarr" and qn in ("DVDSCR", "REGIONAL", "TELECINE", "TELESYNC", "CAM", "WORKPRINT", "DVD-R")):
                continue
            s.add(f"INSERT INTO {app}_quality_definitions (name, quality_name, min_size, max_size, preferred_size) VALUES ('Unlimited', {q(qn)}, 0, 0, {pref});")
    # Movies wait 48 hours and TV 60 minutes for a better release, even for remuxes; a score of BYPASS or more
    # (Top Remux, Tier 1 Group) grabs at once. In the DV libraries only Tier 1 Group (TV) reaches it.
    for name, delay in (("Movies", MOVIE_DELAY), ("TV", TV_DELAY)):
        s.add("INSERT INTO delay_profiles (name, preferred_protocol, usenet_delay, torrent_delay, bypass_if_highest_quality, "
              "bypass_if_above_custom_format_score, minimum_custom_format_score) "
              f"VALUES ({q(name)}, 'only_torrent', NULL, {delay}, 0, 1, {BYPASS});")
    return s


def main():
    OUT.mkdir(exist_ok=True)
    custom_formats().write(OUT / "10.custom-formats.sql", "Custom formats: services, gist tiers, HDR, remux rules, naming-only audio")
    tests().write(OUT / "15.custom-format-tests.sql", "Release-title tests for this fork's custom formats")
    profiles().write(OUT / "20.profiles.sql", "Quality profiles: Best Available (main apps), DV Profile 5 (DV libraries) and 3D (3D library)")
    media().write(OUT / "30.media-management.sql", "Naming, media settings, quality definitions and delay profiles")
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.sql"))))


if __name__ == "__main__":
    main()
