"""Privacy-friendly daily counters, flushed to Supabase every ~60 s.

Never stores raw IPs: visitors are HMAC(daily-salt, ip|ua) hashes, kept in Supabase for 2 days
only to de-duplicate, and the day's total is stored as a plain count. Every call fails safe.
"""
import hashlib
import hmac
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

LOGGER = logging.getLogger(__name__)
BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python|httpx|go-http|java/|axios|node-fetch|headless|"
    r"monitor|uptime|preview|facebookexternalhit|lighthouse|pingdom|scanner|^$",
    re.I,
)
MAX_VISITORS_PER_DAY = 20000  # ponytail: memory cap per day; beyond it uniques undercount, views stay exact

_lock = threading.Lock()
_counts = defaultdict(Counter)   # day -> Counter(views, signups, videos, failures, renders)
_secs = defaultdict(float)       # day -> total render seconds
_visitors = defaultdict(set)     # day -> {hash}
_seen_signups = set()
_cfg = {"url": "", "key": "", "secret": ""}
_state = {"last_flush": None, "last_error": None}


def configure(url, key, secret=""):
    _cfg.update(url=url.rstrip("/"), key=key, secret=secret or key)


def _enabled():
    return bool(_cfg["url"] and _cfg["key"])


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def is_bot(ua):
    return bool(BOT_RE.search(ua or ""))


def visitor_hash(ip, ua, day):
    salt = hashlib.sha256(f"{_cfg['secret']}:{day}".encode()).digest()
    return hmac.new(salt, f"{ip}|{ua}".encode(), hashlib.sha256).hexdigest()[:32]


def page_view(ip, ua):
    if not _enabled() or is_bot(ua):
        return
    day = _today()
    with _lock:
        _counts[day]["views"] += 1
        if len(_visitors[day]) < MAX_VISITORS_PER_DAY:
            _visitors[day].add(visitor_hash(ip, ua, day))


def signup(user_id):
    if not _enabled():
        return
    with _lock:
        if user_id in _seen_signups:
            return
        _seen_signups.add(user_id)  # ponytail: per-process dedupe; a restart can recount one signup
        _counts[_today()]["signups"] += 1


def render_done(videos, failed, seconds):
    if not _enabled():
        return
    with _lock:
        c = _counts[_today()]
        c["videos"] += videos
        c["failures"] += failed
        if videos:
            c["renders"] += videos
            _secs[_today()] += seconds


def flush():
    """Send pending counters; on any error put them back so the next tick retries."""
    if not _enabled():
        return
    with _lock:
        counts, secs, vis = dict(_counts), dict(_secs), dict(_visitors)
        _counts.clear(); _secs.clear(); _visitors.clear()
    headers = {"apikey": _cfg["key"], "Authorization": f"Bearer {_cfg['key']}"}
    for day in counts.keys() | vis.keys():
        c = counts.get(day, Counter())
        body = {
            "p_day": day, "p_views": c["views"], "p_signups": c["signups"], "p_videos": c["videos"],
            "p_failures": c["failures"], "p_render_secs": secs.get(day, 0.0), "p_renders": c["renders"],
            "p_visitors": sorted(vis.get(day, ())),
        }
        try:
            r = requests.post(f"{_cfg['url']}/rest/v1/rpc/noor_analytics_bump", json=body, headers=headers, timeout=10)
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            _state.update(last_flush=datetime.now(timezone.utc).isoformat(), last_error=None)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {str(exc)[:200]}".replace(_cfg["key"], "***")
            _state["last_error"] = msg
            LOGGER.warning("analytics flush failed (will retry): %s", msg)
            with _lock:
                _counts[day].update(c)
                _secs[day] += secs.get(day, 0.0)
                _visitors[day] |= vis.get(day, set())


def status():
    with _lock:
        buffered = sum(c["views"] for c in _counts.values())
    return {"enabled": _enabled(), "buffered_views": buffered, **_state}


def read_days(days=30):
    """Last `days` rows, newest first; [] on any failure."""
    if not _enabled():
        return []
    try:
        r = requests.get(
            f"{_cfg['url']}/rest/v1/noor_analytics_daily",
            params={"select": "*", "order": "day.desc", "limit": str(days)},
            headers={"apikey": _cfg["key"], "Authorization": f"Bearer {_cfg['key']}"}, timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:
        msg = f"read: {type(exc).__name__}: {str(exc)[:200]}".replace(_cfg["key"], "***")
        _state["last_error"] = msg
        LOGGER.warning("analytics read failed: %s", msg)
        return []
    for row in rows:
        row["avg_render_secs"] = round(row["render_secs"] / row["renders"], 1) if row.get("renders") else 0
    return rows


def start(interval=60):
    def loop():
        while True:
            time.sleep(interval)
            flush()
    threading.Thread(target=loop, name="analytics-flush", daemon=True).start()
