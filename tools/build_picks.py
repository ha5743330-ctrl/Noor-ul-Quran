"""Build data/picks_default.json + data/ayah_durations_seed.json from data/picks_seed/*.txt.

    python tools/build_picks.py [path/to/ayah_durations.json]

Seed files hold "=== URDU ONLY ... ===" / "=== ARABIC ONLY ... ===" sections; names are ignored,
only the Surah:Ayah[-Ayah] refs are read. Duplicates are dropped only inside one bucket x mode.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import picks  # noqa: E402

FILES = {30: "30-sec-reference.txt", 60: "1-min-reference.txt", 120: "2-min-reference.txt"}


def main():
    cache = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "cache" / "ayah_durations.json").read_text("utf-8"))
    cache = {k: v for k, v in cache.items() if isinstance(v, (int, float)) and v > 0}
    (ROOT / "data" / "ayah_durations_seed.json").write_text(
        json.dumps(dict(sorted(cache.items())), indent=0) + "\n", encoding="utf-8")
    seconds_of = picks.seconds_lookup(cache)
    quran = json.loads((ROOT / "data" / "quran.json").read_text("utf-8"))
    out = []
    for bucket, name in FILES.items():
        mode = None
        seen = set()
        for line in (ROOT / "data" / "picks_seed" / name).read_text("utf-8").splitlines():
            if line.startswith("==="):
                mode = "urdu" if "URDU" in line.upper() else "arabic"
                seen = set()
                continue
            m = picks.REF_RE.search(line)
            if not (m and mode):
                continue
            ref = picks.normalize(int(m[1]), int(m[2]), int(m[3] or m[2]), quran)
            if ref in seen:
                continue
            seen.add(ref)
            out.append(picks.make_pick(f"{bucket}{mode[0]}{len(seen):03d}", bucket, mode, ref, seconds_of))
    (ROOT / "data" / "picks_default.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    missing = sum(p["seconds"] is None for p in out)
    over = sum(p["over_bucket"] for p in out)
    print(f"{len(out)} picks, {missing} without duration, {over} over bucket")


if __name__ == "__main__":
    main()
