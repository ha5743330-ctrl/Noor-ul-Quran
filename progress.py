"""Real job progress: stage -> overall percent, ETA from a rolling render-rate stat. Pure + tiny JSON files."""
import json
from pathlib import Path

# (stage, start %, end %) of one reel; upload is reported by the server, not make.py.
STAGES = {"audio": (0, 20), "text": (20, 25), "frames": (25, 55), "video": (55, 95), "upload": (95, 100)}


def percent(stage, frac=0.0, reel=1, reels=1):
    """Overall 0-100 for `frac` (0-1) through `stage` of reel `reel` of `reels` (reels are equal shares)."""
    lo, hi = STAGES[stage]
    within = lo + (hi - lo) * min(1.0, max(0.0, frac))
    return round(min(100.0, ((reel - 1) * 100 + within) / max(1, reels)), 1)


def eta(pct, video_seconds, rate):
    """Seconds left = rate (render s per video s) * video seconds * unfinished share; None without a rate."""
    if not rate or not video_seconds or pct is None:
        return None
    return max(0, round(rate * video_seconds * (1 - pct / 100)))


def write(path, stage, frac=0.0, reel=1, reels=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stage": stage, "percent": percent(stage, frac, reel, reels)}), encoding="utf-8")
    tmp.replace(path)


def read(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if d.get("stage") in STAGES and isinstance(d.get("percent"), (int, float)) else None
    except (OSError, ValueError):
        return None


def load_rate(path):
    try:
        rates = [r for r in json.loads(Path(path).read_text(encoding="utf-8")) if isinstance(r, (int, float)) and r > 0]
    except (OSError, ValueError):
        return None
    return sum(rates) / len(rates) if rates else None


def record_rate(path, rate, keep=10):
    try:
        old = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = []
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps((old + [rate])[-keep:]), encoding="utf-8")


def out_seconds(line):
    """Seconds encoded so far from one ffmpeg `-progress` line, else None (N/A, junk, other keys)."""
    key, _, val = line.strip().partition("=")
    try:
        if key in ("out_time_us", "out_time_ms"):  # both are microseconds in ffmpeg
            return max(0.0, int(val) / 1e6)
        if key == "out_time":
            h, m, s = val.split(":")
            return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
    except ValueError:
        pass
    return None
