"""Pure helpers for the managed Picks list: ref parsing/validation, seconds, import/export text."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUCKETS = (30, 60, 120)
MODES = ("urdu", "arabic")
MODE_TO_CONTENT = {"urdu": "urdu_only", "arabic": "arabic_only"}
REF_RE = re.compile(r"(\d{1,3}):(\d{1,3})(?:-(\d{1,3}))?")
_DEFAULTS = json.loads((ROOT / "data" / "reciters.json").read_text(encoding="utf-8"))["defaults"]
DEFAULT_AR, DEFAULT_UR = _DEFAULTS["ar"], _DEFAULTS["ur"]


def split_ref(ref):
    m = REF_RE.fullmatch(ref.strip()) if isinstance(ref, str) else None
    if not m:
        raise ValueError("Use Surah:Ayah or Surah:Start-End, e.g. 2:255 or 18:9-12.")
    return int(m[1]), int(m[2]), int(m[3] or m[2])


def normalize(surah, start, end, quran):
    """Canonical 's:a' / 's:a-b' string; ValueError when the surah or range does not exist."""
    info = quran["surahs"].get(str(surah))
    if info is None:
        raise ValueError(f"Surah {surah} does not exist (1-114).")
    if not 1 <= start <= end <= int(info["n"]):
        raise ValueError(f"Surah {surah} has {info['n']} ayat; {start}-{end} is not a valid range.")
    return f"{surah}:{start}" if start == end else f"{surah}:{start}-{end}"


def parse_ref(ref, quran):
    """Strictly parse one ref -> (canonical, surah, start, end)."""
    s, a, b = split_ref(ref)
    return normalize(s, a, b, quran), s, a, b


def seconds_lookup(cache):
    """seconds_of(mode, surah, start, end) over a durations dict, using the default reciter + Urdu voice."""
    def seconds_of(mode, surah, start, end):
        kind, rid = ("arabic", DEFAULT_AR) if mode == "arabic" else ("urdu", DEFAULT_UR)
        parts = [cache.get(f"{kind}:{rid}:{surah}:{a}") for a in range(start, end + 1)]
        return None if None in parts else round(sum(parts), 2)
    return seconds_of


def make_pick(pick_id, bucket, mode, ref, seconds_of, **extra):
    s, a, b = split_ref(ref)
    seconds = seconds_of(mode, s, a, b)
    return {
        "id": pick_id, "bucket": bucket, "mode": mode, "ref": ref, "seconds": seconds,
        "over_bucket": seconds is not None and seconds > bucket, **extra,
    }


def parse_import(text, quran, default_mode=None):
    """Seed-file lines -> ([(mode, canonical_ref)], [invalid line descriptions]).

    "=== URDU ... ===" / "=== ARABIC ... ===" headers switch mode; names in the line are ignored.
    """
    mode, found, invalid = default_mode, [], []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("==="):
            upper = line.upper()
            mode = "urdu" if "URDU" in upper else "arabic" if "ARABIC" in upper else mode
            continue
        m = REF_RE.search(line)
        if not m:
            invalid.append(f"line {number}: no Surah:Ayah found ({line[:60]})")
        elif mode not in MODES:
            invalid.append(f"line {number}: no Urdu/Arabic section header ({line[:60]})")
        else:
            try:
                found.append((mode, normalize(int(m[1]), int(m[2]), int(m[3] or m[2]), quran)))
            except ValueError as exc:
                invalid.append(f"line {number}: {exc}")
    return found, invalid


def format_export(picks, quran):
    """Same line format as the seed files, one section per mode."""
    out = []
    for mode in MODES:
        out.append(f"=== {mode.upper()} ONLY REFERENCES ===")
        for n, p in enumerate((p for p in picks if p["mode"] == mode), 1):
            s, a, b = split_ref(p["ref"])
            out.append(f"{n}. (Surah {quran['surahs'][str(s)]['english']} {p['ref']})")
        out.append("")
    return "\n".join(out)
