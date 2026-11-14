#!/usr/bin/env python3
"""Generate tweaks/*.sql: this fork's custom formats, profiles and media management.

Edit the tables below, run `python3 tools/gen_tweaks.py`, then `python3 tools/build_db.py`.
The generated SQL is committed; this script is the source of truth for it.

Scoring scale (scores only compare releases of the same quality; quality order decides first):

    BAN            -999999  never grabbed (minimum score is -100000)
    edition        -5000    Special Edition: theatrical cut is the default
    HDR present    +10000   2160p WEB-DL only: any HDR beats any SDR
    source tier    1000s    gist tier: SDR or HDR list, DV+HDR10 counts as HDR (T1 +2000 … bad -1000);
                            the UHD DV list only ranks Profile 5 in the DV profile
    HDR type       +100s    DV+HDR10 +200 > HDR10+ +100 > HDR10 0, within a tier
    repack, IMAX   +100s    trigger an upgrade (upgrade increment is 100)
    tiebreakers    < 100    season pack 50, gist position <= 30, labels 4, group tier 1-3;
                            these only choose the first grab, never trigger an upgrade
    remux groups   +100s    CiNEPHiLES 400 > Remux Tier 01 300 > 02 200 > 03 100
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
WEB_AFTER = r"(?=[ ._-]+web[ ._-]?(dl|rip)?\b)"
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
    "PMTP": ("Paramount+", r"\b(pmtp|paramount[ ._-]?(\+|plus))(?=[ ._-])"),
    "SHO": ("Showtime", r"\b(sho|showtime)\b"),
    "DSNP": ("Disney+", r"\b(dsnp|dsny|strp|disney[ ._-]?(\+|plus)|star[ ._-]?(\+|plus))(?=[ ._-])"),
    "HMAX": ("HBO Max", r"\b(hmax|hbom|hbo[ ._-]?max)\b"),
    "MAX": ("Max", r"(?<!hbo[ ._-])\b(max)\b"),
    "BCORE": ("Bravia Core", r"\b(bcore|bravia[ ._-]core|core)\b"),
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
# Bravia Core: remux-like bitrates, so it beats every other WEB-DL and always triggers an upgrade.
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
}

# Mutually exclusive audio formats, printed in file names (consumer names).
AUDIO_NAMES = [
    ("TrueHD Atmos", [r"\b(TrueHD)\b", r"\b(Atmos)\b"], []),
    ("TrueHD", [r"\b(TrueHD)\b"], [r"\b(Atmos)\b"]),
    ("DTS:X", [r"\bDTS[ ._:-]?X(?!\d)"], []),
    ("DTS-HD MA", [r"\bDTS[ ._-]?HD[ ._-]?MA\b"], [r"\bDTS[ ._:-]?X(?!\d)"]),
    ("LPCM", [r"\b(L?PCM)\b"], []),
    ("FLAC", [r"\b(FLAC)\b"], []),
    ("DD+ Atmos", [r"\b(DDP|DD\+|E-?AC-?3)", r"\b(Atmos)\b"], [r"\b(TrueHD)\b"]),
    ("DD+", [r"\b(DDP|DD\+|E-?AC-?3)"], [r"\b(Atmos)\b"]),
    ("DD", [r"\b(DD|AC-?3)(?![+P])\d?"], [r"\b(DDP|DD\+|E-?AC-?3|TrueHD)"]),
    ("DTS", [r"\bDTS\b"], [r"\bDTS[ ._-]?(HD|X|ES|HRA)\b|\bDTS:X"]),
    ("AAC", [r"\b(AAC)\b"], []),
    ("Opus", [r"\b(OPUS)\b"], []),
]

BANS_COMMON = ["BR-DISK", "BR-DISK (BTN)", "3D", "Upscaled", "LQ", "LQ (Release Title)", "Generated Dynamic HDR",
               "Bad Dual Groups", "Line/Mic Dubbed", "AV1", "Sing-Along Versions", "Black and White Editions",
               "Extras", "x265 (no HDR/DV)", "Retags", "Obfuscated", "Open Matte", "Anime LQ Groups",
               "Language: Not Original", "Lossy Remux"]

LADDER_SCORES = {
    "Special Edition": -5000,
    "Repack/Proper": 100, "Repack2": 200, "Repack3": 300,
    "IMAX": 100, "IMAX Enhanced": 100,
    "Season Pack": 50,
    "Criterion Collection": 4, "Masters of Cinema": 4, "Vinegar Syndrome": 4, "Remaster": 4, "4K Remaster": 4,
    "WEB Tier 01": 3, "WEB Tier 02": 2, "WEB Tier 03": 1,
    "Anime Web Tier 01": 3, "Anime Web Tier 02": 3, "Anime Web Tier 03": 2, "Anime Web Tier 04": 2,
    "Anime Web Tier 05": 1, "Anime Web Tier 06": 1,
    "CiNEPHiLES": 400, "Remux Tier 01": 300, "Remux Tier 02": 200, "Remux Tier 03": 100,
    "Anime BD Tier 01": 300, "Anime BD Tier 02": 300, "Anime BD Tier 03": 200, "Anime BD Tier 04": 200,
    "Anime BD Tier 05": 100, "Anime BD Tier 06": 100, "Anime BD Tier 07": 100, "Anime BD Tier 08": 100,
    "HDR (2160p WEB)": 10000, "HDR: DV+HDR10": 200, "HDR: HDR10+": 100,
    "DVD Remux": 6000, "SD WEB-DL": 3000,
}

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

    # Naming-only audio formats with consumer names, mutually exclusive.
    for name, must, mustnot in AUDIO_NAMES:
        cf = f"Audio: {name}"
        s.cf(cf, f"Naming only (score 0): prints the audio codec as '{name}'.", rename=0)
        for i, p in enumerate(must):
            s.title(cf, f"has {i}", s.regex(f"sg-audio-{name}-has{i}", p))
        for i, p in enumerate(mustnot):
            s.title(cf, f"not {i}", s.regex(f"sg-audio-{name}-not{i}", p), negate=1)

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
    s.cf("CiNEPHiLES", "Preferred remux group, ranked above TRaSH's Remux Tier 01.")
    s.cond("CiNEPHiLES", "CiNEPHiLES", "release_group", "sg-cinephiles", required=1)
    s.cf("Lossy Remux", "Remux without lossless audio (TrueHD, DTS-HD MA, DTS:X, LPCM or FLAC). Banned.")
    s.cond("Lossy Remux", "Remux", "quality_modifier", "remux", required=1, arr="radarr")
    s.cond("Lossy Remux", "Remux Source", "source", "bluray_raw", required=1, arr="sonarr")
    s.title("Lossy Remux", "Not lossless", "sg-lossless-audio", negate=1)
    s.cond("Lossy Remux", "Not DVD", "source", "dvd", negate=1, required=1)
    s.cf("DVD Remux", "Untouched DVD video. Same quality as a DVD encode in the apps, so ranked by score.")
    s.cond("DVD Remux", "DVD", "source", "dvd", required=1)
    s.title("DVD Remux", "Remux", "sg-remux")
    s.cf("SD WEB-DL", "480p/576p WEB-DL, ranked between DVD Remux and DVD encodes.")
    web_source(s, "SD WEB-DL")
    for r in ("2160p", "1080p", "720p"):
        s.cond("SD WEB-DL", f"Not {r}", "resolution", r, negate=1, required=1)

    # Only spelled-out services, IMAX and Hybrid print via {Custom Formats}; everything else is off.
    printed = [spelled for spelled, _ in SERVICES.values()] + ["IMAX", "IMAX Enhanced", "Hybrid"]
    s.add("UPDATE custom_formats SET include_in_rename = 0;")
    s.add(f"UPDATE custom_formats SET include_in_rename = 1 WHERE name IN ({', '.join(q(n) for n in printed)});")
    return s


def tests() -> SQL:
    s = SQL()
    m, t = "movie", "series"
    for cf, title, should, kind in [
        ("Movies Anywhere", "Camp.Miasma.2023.1080p.WEB-DL.DDP5.1.H.264-GRP", 0, m),
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
        ("BBC iPlayer", "Some.Show.S01E01.1080p.iP.WEB-DL.AAC2.0.H.264-GRP", 1, t),
        ("Disney+", "Idiocracy (2006) {imdb-tt0387808} Disney+ WEBDL-1080p DD+ 5.1-FLUX", 1, m),
        ("Apple TV+", "Some.Show.S01E01.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, t),
        ("Paramount+", "Some.Show.S01E01.1080p.PMTP.WEB-DL.DDP5.1.H.264-GRP", 1, t),
        ("Bravia Core", "Some.Film.2010.2160p.BCORE.WEB-DL.DTS-HD.MA.5.1.HDR.H.265-GRP", 1, m),
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
        ("Audio: DD+ Atmos", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 1, m),
        ("Audio: DD+", "Some.Film.2010.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-GRP", 0, m),
        ("Audio: DTS-HD MA", "Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", 1, m),
        ("Audio: DTS:X", "Heat.1995.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.DTS-X.7.1-GRP", 1, m),
        ("Audio: DTS-HD MA", "Heat.1995.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.DTS-X.7.1-GRP", 0, m),
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


def profiles() -> SQL:
    s = SQL()
    naming = {spelled: 0 for spelled, _ in SERVICES.values()} | {f"Audio: {n}": 0 for n, _, _ in AUDIO_NAMES} | {"Hybrid": 0}

    scores = dict(LADDER_SCORES)
    for lst, tiers in GIST.items():
        if lst == "UHD DV":
            continue
        for tier_score, tags in tiers:
            for i, tag in enumerate(tags):
                scores[f"Gist {lst}: {tag}"] = tier_score + position_bonus(i, len(tags))
    ladder_profile(
        s, "Ladder",
        "One ladder for movies and TV: 2160p Remux (final) > 2160p WEB-DL > 1080p Remux > 1080p WEB-DL > "
        "720p WEB-DL > SD (DVD Remux > SD WEB-DL > DVD encode). WEB-DLs rank by vevv's source gist for their "
        "own HDR type, then HDR type, then group. Remuxes need lossless audio and rank CiNEPHiLES > TRaSH tiers.",
        ["Remux-2160p", "WEBDL-2160p", "Remux-1080p", "WEBDL-1080p", "WEBDL-720p", ("SD", ["DVD", "WEBDL-480p"])],
        top="Remux-2160p", min_score=MIN_SCORE, until_score=MIN_SCORE, scores=scores | naming,
        bans=BANS_COMMON + ["DV (w/o HDR fallback)"])

    p5 = {k: v for k, v in LADDER_SCORES.items() if k.startswith(("Repack", "WEB Tier", "Anime Web", "Season Pack"))}
    p5["DV (w/o HDR fallback)"] = 50000
    for tier_score, tags in GIST["UHD DV"]:
        for i, tag in enumerate(tags):
            p5[f"Gist P5: {tag}"] = tier_score + position_bonus(i, len(tags))
    ladder_profile(
        s, "DV Profile 5",
        "Profile 5 Dolby Vision WEB-DLs only, for the DV libraries (RadarrDV, SonarrDV): 2160p > 1080p, "
        "both ranked by the gist's UHD DV tiers.",
        ["WEBDL-2160p", "WEBDL-1080p"], top="WEBDL-2160p", min_score=40000, until_score=999999, scores=p5 | naming,
        bans=[b for b in BANS_COMMON if b != "Lossy Remux"])
    return s


def media() -> SQL:
    s = SQL()
    details = "{Custom Formats} {Quality Full}{ MediaInfo VideoDynamicRangeType}" + "".join(
        "{ Custom Format:Audio: " + n + "}" for n, _, _ in AUDIO_NAMES) + "{ MediaInfo AudioChannels}{-Release Group}"
    movie_file = "{Movie CleanTitle} ({Release Year}) {imdb-{ImdbId}} {edition-{Edition Tags}} " + details
    s.add("INSERT INTO radarr_naming (name, rename, movie_format, movie_folder_format, replace_illegal_characters, colon_replacement_format) "
          f"VALUES ('Ladder', 1, {q(movie_file)}, {q('{Movie CleanTitle} ({Release Year}) {imdb-{ImdbId}}')}, 1, 'smart');")
    series = "{Series CleanTitleWithoutYear} ({Series Year})"
    s.add("INSERT INTO sonarr_naming (name, rename, standard_episode_format, daily_episode_format, anime_episode_format, "
          "series_folder_format, season_folder_format, replace_illegal_characters, colon_replacement_format, multi_episode_style) VALUES ("
          f"'Ladder', 1, {q(series + ' - S{season:00}E{episode:00} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' - {Air-Date} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' - S{season:00}E{absolute:0000} - {Episode CleanTitle} - ' + details)}, "
          f"{q(series + ' {tvdb-{TvdbId}}')}, 'Season {{season:00}}', 1, 4, 0);".replace("{{", "{").replace("}}", "}"))
    for app in ("radarr", "sonarr"):
        s.add(f"INSERT INTO {app}_media_settings (name, propers_repacks, enable_media_info) VALUES ('Ladder', 'doNotPrefer', 1);")
        pref = 1999 if app == "radarr" else 995
        for qn in ALL_QUALITIES:
            if qn in ("Raw-HD", "BR-DISK") or (app == "sonarr" and qn in ("DVDSCR", "REGIONAL", "TELECINE", "TELESYNC", "CAM", "WORKPRINT", "DVD-R")):
                continue
            s.add(f"INSERT INTO {app}_quality_definitions (name, quality_name, min_size, max_size, preferred_size) VALUES ('Unlimited', {q(qn)}, 0, 0, {pref});")
    s.add("INSERT INTO delay_profiles (name, preferred_protocol, usenet_delay, torrent_delay, bypass_if_highest_quality) "
          "VALUES ('No Delay', 'only_torrent', NULL, 0, 0);")
    return s


def main():
    OUT.mkdir(exist_ok=True)
    custom_formats().write(OUT / "10.custom-formats.sql", "Custom formats: services, gist tiers, HDR, remux rules, naming-only audio")
    tests().write(OUT / "15.custom-format-tests.sql", "Release-title tests for this fork's custom formats")
    profiles().write(OUT / "20.profiles.sql", "Quality profiles: Ladder (main apps) and DV Profile 5 (DV libraries)")
    media().write(OUT / "30.media-management.sql", "Naming, media settings, quality definitions and delay profile")
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.sql"))))


if __name__ == "__main__":
    main()
