#!/usr/bin/env python3
"""Replay this PCD into a scratch SQLite database, the way Profilarr layers it.

Order: schema dependency ops -> ops/ -> tweaks/, each sorted by numeric prefix.
The schema dependency is cloned into .deps/schema at the version in pcd.json.

    python3 tools/build_db.py            # builds .build/pcd.db, exits non-zero on SQL errors
"""
import json, re, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".build" / "pcd.db"


def ordered(d: Path) -> list[Path]:
    key = lambda p: (int(m.group(1)) if (m := re.match(r"(\d+)", p.name)) else 10**9, p.name)
    return sorted(d.glob("*.sql"), key=key)


def schema_dir() -> Path:
    url, ver = next(iter(json.loads((ROOT / "pcd.json").read_text())["dependencies"].items()))
    dest = ROOT / ".deps" / "schema"
    if not dest.exists():
        subprocess.run(["git", "clone", "-q", url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--tags"], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", ver], check=True)
    return dest / "ops"


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    OUT.unlink(missing_ok=True)
    db = sqlite3.connect(OUT)
    db.execute("PRAGMA foreign_keys = ON")
    for layer, d in (("schema", schema_dir()), ("ops", ROOT / "ops"), ("tweaks", ROOT / "tweaks")):
        files = ordered(d) if d.exists() else []
        for f in files:
            try:
                db.executescript(f.read_text())
            except sqlite3.Error as e:
                print(f"{layer}/{f.name}: {e}", file=sys.stderr)
                return 1
        print(f"{layer}: {len(files)} files")
    db.commit()
    bad = db.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        print(f"foreign key violations: {bad[:10]}", file=sys.stderr)
        return 1
    print(f"built {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
