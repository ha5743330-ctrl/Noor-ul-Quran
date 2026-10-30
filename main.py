import base64
import functools
import json
import hashlib
import hmac
import io
import logging
import ntpath
import os
import random
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, features
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import picks as picks_lib

# Render sets RENDER=true; its 512 MB plan OOMs on the full profile (measured 576 MiB for 30 s fast), so default to low.
if os.getenv("RENDER") and not os.getenv("RENDER_MEMORY_PROFILE"):
    os.environ["RENDER_MEMORY_PROFILE"] = "low"
logging.basicConfig(level=logging.INFO)  # render start/end/peak lines must reach Render Logs
LOGGER = logging.getLogger(__name__)
RAQM_AVAILABLE = bool(features.check("raqm"))
RAQM_REQUIRED_ERROR = (
    "Arabic/Urdu text rendering is unavailable because this Pillow build lacks "
    "RAQM text shaping. Install a RAQM-enabled Pillow build and restart the service."
)

# 1. Environment Variables Load Karein
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
MEDIA_BUCKET = os.getenv("SUPABASE_MEDIA_BUCKET", "noor-media")
BACKGROUNDS_BUCKET = os.getenv("SUPABASE_BACKGROUNDS_BUCKET", "noor-backgrounds")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
HISTORY_FILE = OUTPUT_DIR / "history.json"
BACKGROUND_DIR = Path(os.getenv("BACKGROUNDS_DIR", BASE_DIR / "backgrounds"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", BASE_DIR / "cache"))
CLIP_CACHE_DIR = CACHE_DIR / "backgrounds"
MUSIC_CACHE_DIR = CACHE_DIR / "music"
MEDIA_NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z")
VIDEO_URL_SECRET = os.getenv("VIDEO_URL_SECRET") or secrets.token_urlsafe(32)
VIDEO_URL_TTL_SECONDS = 10 * 60
VIDEO_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\.mp4\Z")
MEDIA_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\.(?:mp4|png)\Z")
HISTORY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
MAX_BULK_HISTORY_ITEMS = 100
MAX_AYAHS_PER_REQUEST = 50
RENDER_TIMEOUT_SECONDS = int(os.getenv("RENDER_TIMEOUT_SECONDS", "900"))
IMAGE_COOLDOWN_SECONDS = 10
# Free-tier knobs (Render free 512MB / Supabase free 1GB).
CLEANUP_DAYS = float(os.getenv("CLEANUP_DAYS", "3"))
CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))
PREMIUM_DAILY_LIMIT = int(os.getenv("PREMIUM_DAILY_LIMIT") or 0) or None  # None = same as DAILY_LIMIT
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "35"))
DEFAULT_QUALITY = os.getenv("DEFAULT_QUALITY", "fast")
MAX_ACTIVE_JOBS_PER_USER = int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "1"))
DEFAULT_RENDER_SECONDS = 90.0
AUTH_CACHE_SECONDS = 60
RECITERS_PATH = BASE_DIR / "data" / "reciters.json"
RECITER_CONFIG = json.loads(RECITERS_PATH.read_text(encoding="utf-8"))
RECITERS_BY_ID = {
    item["id"]: item for item in RECITER_CONFIG["reciters"]
}
DEFAULT_ARABIC_RECITER_ID = RECITER_CONFIG["defaults"]["ar"]
DEFAULT_URDU_VOICE_ID = RECITER_CONFIG["defaults"]["ur"]
MAX_BACKGROUND_BYTES = 50 * 1024 * 1024
VIDEO_STYLE_ELEMENTS = {"bismillah", "surah", "label", "arabic", "urdu", "english", "channel"}
# Quran text always renders in the verified KFGQPC font; only its color can change.
QURAN_STYLE_ELEMENTS = {"bismillah", "arabic"}
RENDER_QUALITIES = {"fast", "balanced", "high"}
# Memory guard. Peak MiB of one render ~= RENDER_BASE_MB + per-second * seconds (measured in a 512 MB Docker
# container, make.py only: low 30 s fast 278, 58 s high 299; full 30 s fast 576, 58 s high 1390).
# 0 disables the guard (default off-Render); Render defaults to its 512 MB plan.
RENDER_MEMORY_LIMIT_MB = int(os.getenv("RENDER_MEMORY_LIMIT_MB", "512" if os.getenv("RENDER") else "0"))
SERVER_BASE_MB = 100  # idle API ~70 MiB measured + request overhead
RENDER_BASE_MB = 265
RENDER_MB_PER_SECOND = {
    "low": {"fast": 0.4, "balanced": 0.4, "high": 0.6},
    "full": {"fast": 10.4, "balanced": 10.4, "high": 19.4},
}


def _estimate_render_mb(seconds, quality):
    profile = "low" if os.getenv("RENDER_MEMORY_PROFILE", "").lower() == "low" else "full"
    return SERVER_BASE_MB + RENDER_BASE_MB + RENDER_MB_PER_SECOND[profile][quality] * seconds


def _check_render_memory(seconds, quality):
    if not RENDER_MEMORY_LIMIT_MB:
        return
    needed = _estimate_render_mb(seconds, quality)
    if needed > RENDER_MEMORY_LIMIT_MB:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A {seconds} s {quality}-quality video needs about {round(needed)} MB, more than this "
                f"server's {RENDER_MEMORY_LIMIT_MB} MB. Choose a shorter length or lower quality."
            ),
        )
VIDEO_STYLE_FONTS = {
    "amiri_regular",
    "amiri_bold",
    "naskh_regular",
    "naskh_bold",
    "nastaliq_regular",
    "nastaliq_bold",
}
ALLOWED_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
}
MUSIC_FILE_EXTENSIONS = {".mp3", ".mpeg", ".wav", ".aac", ".m4a"}


def _redact_secrets(message: str) -> str:
    for secret in (SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, VIDEO_URL_SECRET):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


# 2. FastAPI App Initialize Karein
app = FastAPI(title="Noor ul Quran API Engine")
GENERATION_LOCK = Lock()
GENERATION_JOBS_LOCK = Lock()
HISTORY_LOCK = Lock()
FEATURED_IMAGE_LOCK = Lock()
LAST_FEATURED_AYAH = None
GENERATION_JOBS = {}
# ponytail: in-memory; resets on restart/spin-down. Persist in Supabase if abused.
DAILY_USAGE = {}
DAILY_USAGE_LOCK = Lock()
RENDER_DURATIONS = []  # seconds per reel, last 10 completed jobs
RENDER_RATES = []  # render seconds per video second, last 10 completed jobs
DEFAULT_RENDER_RATE = 3.0
AUTH_CACHE = {}
AUTH_CACHE_LOCK = Lock()
LAST_IMAGE_RENDER = {}
EPHEMERAL_MEDIA_READY = False
GENERATION_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quran-render")

# 3. CORS Setup (Frontend Integration ke liye)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Output Directory Mount Karein (Rendered Videos Access karne ke liye)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)

# 5. Request Body Schema
def _daily_limit(user):
    return PREMIUM_DAILY_LIMIT if PREMIUM_DAILY_LIMIT and user.get("premium_access") else DAILY_LIMIT


class GenerateRequest(BaseModel):
    mode: str  # 'pick', 'picks', 'random'
    pick_ids: Optional[list[str]] = Field(default=None, max_length=25)  # mode 'picks': ids from /api/picks
    verse: Optional[str] = None  # e.g. "55:1-8"
    random_count: int = 1
    max_duration: int = 60
    quality: str = DEFAULT_QUALITY
    content_mode: str = "full"
    reciter_id: Optional[str] = None
    urdu_voice_id: Optional[str] = None
    styles: Any = Field(default_factory=dict)
    music: Optional[str] = None  # Background music filename (e.g. "soft_tone.mp3")


class PremiumAccessRequest(BaseModel):
    enabled: bool


class HistoryBulkRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=MAX_BULK_HISTORY_ITEMS)


class GenerateImageRequest(BaseModel):
    ayah: Optional[str] = Field(default=None, max_length=16)


def validate_video_styles(styles):
    if not isinstance(styles, dict):
        raise HTTPException(status_code=400, detail="Styles must be an object.")
    for element, style in styles.items():
        if element not in VIDEO_STYLE_ELEMENTS or not isinstance(style, dict):
            raise HTTPException(status_code=400, detail="Invalid video style element.")
        if any(field not in {"color", "font"} for field in style):
            raise HTTPException(status_code=400, detail="Invalid video style field.")
        color = style.get("color")
        if color is not None and (
            not isinstance(color, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
        ):
            raise HTTPException(status_code=400, detail="Style colors must use #RRGGBB format.")
        font = style.get("font")
        if font is not None and element in QURAN_STYLE_ELEMENTS:
            raise HTTPException(status_code=400, detail="Quran text always uses the verified KFGQPC font.")
        if font is not None and (
            not isinstance(font, str) or font not in VIDEO_STYLE_FONTS
        ):
            raise HTTPException(status_code=400, detail="Invalid video style font.")


def _supabase_request(method: str, path: str, token: str, **kwargs):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=503, detail="Supabase is not configured on the server.")
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {token}",
        **kwargs.pop("headers", {}),
    }
    try:
        return requests.request(
            method,
            f"{SUPABASE_URL}{path}",
            headers=headers,
            timeout=15,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Could not reach Supabase.") from exc


def _storage_request(method: str, path: str, **kwargs):
    if not SUPABASE_SERVICE_KEY:
        raise HTTPException(status_code=503, detail="Supabase Storage service key is not configured on the backend.")
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        **kwargs.pop("headers", {}),
    }
    try:
        response = requests.request(
            method,
            f"{SUPABASE_URL}/storage/v1{path}",
            headers=headers,
            timeout=60,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Could not reach Supabase Storage.") from exc
    if not response.ok and response.status_code != 404:
        if response.status_code == 413:
            raise HTTPException(status_code=413, detail="Supabase Free Storage accepts files up to 50 MB.")
        raise HTTPException(status_code=502, detail="Supabase Storage request failed; check the media bucket and server key.")
    return response


def _list_media_objects(prefix: str, bucket: str = MEDIA_BUCKET):
    response = _storage_request(
        "POST",
        f"/object/list/{quote(bucket, safe='')}",
        json={"prefix": prefix, "limit": 100, "offset": 0, "sortBy": {"column": "name", "order": "asc"}},
    )
    return response.json()


def _media_object_exists(object_path: str) -> bool:
    prefix, filename = object_path.rsplit("/", 1)
    return any(
        item.get("name") in {filename, object_path}
        for item in _list_media_objects(prefix)
    )


def _upload_media_file(local_path: Path, object_path: str, content_type: str, bucket: str = MEDIA_BUCKET):
    if local_path.stat().st_size > MAX_BACKGROUND_BYTES:
        raise HTTPException(status_code=413, detail="Supabase Free Storage accepts files up to 50 MB.")
    with local_path.open("rb") as media_file:
        _storage_request(
            "POST",
            f"/object/{quote(bucket, safe='')}/{quote(object_path, safe='/')}",
            data=media_file,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )


def _delete_media_objects(object_paths):
    if not SUPABASE_SERVICE_KEY:
        return
    paths = sorted({
        path
        for path in object_paths
        if isinstance(path, str)
        and path.startswith(("videos/", "ayah-images/", "avatars/"))
        and ".." not in path.split("/")
    })
    if paths:
        _storage_request(
            "DELETE",
            f"/object/{quote(MEDIA_BUCKET, safe='')}",
            json={"prefixes": paths},
        )


def _download_media_file(object_path: str, local_path: Path, bucket: str = MEDIA_BUCKET) -> bool:
    response = _storage_request(
        "GET",
        f"/object/authenticated/{quote(bucket, safe='')}/{quote(object_path, safe='/')}",
        stream=True,
    )
    if response.status_code == 404:
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    partial = local_path.with_name(f".{uuid4().hex}.part")
    try:
        with partial.open("wb") as local_file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    local_file.write(chunk)
        partial.replace(local_path)
    finally:
        partial.unlink(missing_ok=True)
    return True


def _remove_bucket_object(bucket: str, object_path: str):
    _storage_request("DELETE", f"/object/{quote(bucket, safe='')}", json={"prefixes": [object_path]})


def _sync_cached_object(bucket: str, object_path: str, local_path: Path, size=None):
    """Stream an object into the cache unless a same-size copy is already there."""
    if local_path.is_file() and (size is None or local_path.stat().st_size == size):
        return
    _download_media_file(object_path, local_path, bucket)


def _list_bucket_files(prefix: str, bucket: str, extensions):
    return [
        (Path(item["name"]).name, (item.get("metadata") or {}).get("size"))
        for item in _list_media_objects(prefix, bucket)
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and Path(item["name"]).suffix.lower() in extensions
    ]


def _sync_render_media(music: Optional[str]):
    """Fetch backgrounds and the chosen music into cache/ on demand; failures degrade to local/still fallbacks."""
    if not SUPABASE_SERVICE_KEY:
        return
    try:
        for name, size in _list_bucket_files("", BACKGROUNDS_BUCKET, {".mp4"}):
            _sync_cached_object(BACKGROUNDS_BUCKET, name, CLIP_CACHE_DIR / name, size)
    except Exception as exc:
        LOGGER.warning("Background clips unavailable, using still image: %s", _redact_secrets(str(exc)))
    if music:
        try:
            sizes = dict(_list_bucket_files("music", MEDIA_BUCKET, MUSIC_FILE_EXTENSIONS))
            if music in sizes:
                _sync_cached_object(MEDIA_BUCKET, f"music/{music}", MUSIC_CACHE_DIR / music, sizes[music])
        except Exception as exc:
            LOGGER.warning("Music unavailable, rendering without it: %s", _redact_secrets(str(exc)))


def _signed_media_url(object_path: str) -> str:
    response = _storage_request(
        "POST",
        f"/object/sign/{quote(MEDIA_BUCKET, safe='')}/{quote(object_path, safe='/')}",
        json={"expiresIn": 7 * 24 * 60 * 60},
    )
    signed_path = response.json()["signedURL"]
    if signed_path.startswith("http"):
        return signed_path
    return f"{SUPABASE_URL}/storage/v1{signed_path}"


def _prepare_ephemeral_media():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not SUPABASE_SERVICE_KEY:
        return

    for object_path, local_path in (
        ("metadata/captions.csv", OUTPUT_DIR / "captions.csv"),
        ("state/used.json", CACHE_DIR / "used.json"),
    ):
        if _media_object_exists(object_path):
            _download_media_file(object_path, local_path)


def _raise_supabase_error(response: requests.Response):
    if response.ok:
        return
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Session is invalid or expired. Please sign in again.")
    if response.status_code == 404:
        raise HTTPException(status_code=503, detail="Run schema.sql in the Supabase SQL Editor to enable access control.")
    raise HTTPException(status_code=502, detail="Supabase access check failed.")


class AccountError(Exception):
    """Blocked / deactivated account; rendered as 403 {"error": code, "reason": ...}."""

    def __init__(self, code, reason=""):
        self.code, self.reason = code, reason


@app.exception_handler(AccountError)
async def _account_error_handler(request: Request, exc: AccountError):
    message = {
        "account_blocked": "Your account has been blocked." + (f" Reason: {exc.reason}" if exc.reason else ""),
        "account_deactivated": "Your account is deactivated. Open your Profile and click Reactivate.",
    }[exc.code]
    return JSONResponse(status_code=403, content={"error": exc.code, "reason": exc.reason, "detail": message})


def _token_claims(token: str):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def _token_auth_time(token: str) -> int:
    """When the user actually logged in (newest amr timestamp); survives token refreshes, unlike iat."""
    claims = _token_claims(token)
    stamps = [a.get("timestamp") for a in claims.get("amr") or [] if isinstance(a, dict)]
    stamps = [t for t in stamps if isinstance(t, (int, float))]
    return int(max(stamps)) if stamps else int(claims.get("iat") or 0)


def _authenticate(authorization, allow_deactivated=False):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    token = authorization[7:].strip()
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with AUTH_CACHE_LOCK:
        cached = AUTH_CACHE.get(cache_key)
    if cached and cached[0] > now:
        user = dict(cached[1])
    else:
        user = _fetch_current_user(token)
        with AUTH_CACHE_LOCK:
            if len(AUTH_CACHE) >= 1000:
                AUTH_CACHE.clear()
            AUTH_CACHE[cache_key] = (now + AUTH_CACHE_SECONDS, user)
        user = dict(user)
    # Per request, never cached with the token: admin actions must bite on live tokens.
    state = _users_state().get(user["id"]) or {}
    if state.get("blocked"):
        raise AccountError("account_blocked", state.get("reason", ""))
    if state.get("deactivated") and not allow_deactivated:
        raise AccountError("account_deactivated")
    if state.get("signed_out_at") and _token_auth_time(token) < state["signed_out_at"]:
        raise HTTPException(status_code=401, detail="Session ended. Please sign in again.")
    return user


def get_current_user(authorization: Optional[str] = Header(default=None)):
    return _authenticate(authorization)


def get_user_allow_deactivated(authorization: Optional[str] = Header(default=None)):
    return _authenticate(authorization, allow_deactivated=True)


def _fetch_current_user(token: str):
    user_response = _supabase_request("GET", "/auth/v1/user", token)
    _raise_supabase_error(user_response)
    user = user_response.json()

    access_response = _supabase_request(
        "POST",
        "/rest/v1/rpc/noor_my_access",
        token,
        json={},
    )
    _raise_supabase_error(access_response)
    access = access_response.json()
    return {
        "id": user["id"],
        "email": user.get("email", ""),
        "is_admin": bool(access.get("is_admin")),
        "premium_access": bool(access.get("premium_access")),
        "token": token,
        "metadata": user.get("user_metadata") or {},
        "email_confirmed": bool(user.get("email_confirmed_at") or user.get("confirmed_at")),
        "new_email": user.get("new_email") or "",
        "created_at": user.get("created_at"),
    }


def require_admin(user=Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


@functools.cache
def _quran_data():
    quran_file = BASE_DIR / "data" / "quran.json"
    if not quran_file.exists():
        raise HTTPException(status_code=503, detail="Quran data is unavailable on the server.")
    try:
        return json.loads(quran_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Quran data could not be read.") from exc


def _validate_ayah_range(surah: int, start_ayah: int, end_ayah: int):
    if start_ayah > end_ayah:
        raise HTTPException(status_code=400, detail="Start ayah must not exceed end ayah.")
    _validate_verse(surah, start_ayah)
    _validate_verse(surah, end_ayah)
    if end_ayah - start_ayah + 1 > MAX_AYAHS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Select at most {MAX_AYAHS_PER_REQUEST} ayat per request.",
        )


def _latest_generated_video() -> Optional[str]:
    if not OUTPUT_DIR.exists():
        return None
    videos = sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return videos[0].name if videos else None


def _featured_entries():
    featured_path = BASE_DIR / "data" / "featured_ayahs.json"
    try:
        featured = json.loads(featured_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.exception("Could not load curated ayah data.")
        raise HTTPException(status_code=500, detail="Curated ayah data is unavailable.") from exc
    entries = featured.get("ayahs") if isinstance(featured, dict) else None
    if not isinstance(entries, list) or not entries or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("ref"), str)
        or re.fullmatch(r"\d{1,3}:\d{1,3}", entry["ref"]) is None
        for entry in entries
    ):
        raise HTTPException(status_code=500, detail="Curated ayah data is invalid.")
    return entries


def _featured_ayah_references():
    return [entry["ref"] for entry in _featured_entries()]


@app.get("/api/featured")
def featured_ayahs():
    return {"ayahs": [
        {"ref": e["ref"], "theme": e.get("theme"), "length": e.get("length")}
        for e in _featured_entries()
    ]}


def _select_featured_ayah(reference=None):
    global LAST_FEATURED_AYAH
    references = _featured_ayah_references()
    with FEATURED_IMAGE_LOCK:
        if reference is not None:
            if reference not in references:
                raise HTTPException(status_code=400, detail="Ayah must be in the curated featured list.")
            LAST_FEATURED_AYAH = reference
            return reference
        choices = [ayah for ayah in references if ayah != LAST_FEATURED_AYAH]
        selected = random.choice(choices or references)
        LAST_FEATURED_AYAH = selected
        return selected


@app.post("/api/generate-image")
def generate_ayah_image(req: GenerateImageRequest, user=Depends(get_current_user)):
    _require_raqm_for_generation("arabic_only")
    reference = _select_featured_ayah(req.ayah)
    surah_number, ayah_number = (int(value) for value in reference.split(":"))
    import make as renderer

    try:
        surah = _quran_data()["surahs"][str(surah_number)]
        arabic_text = renderer.verified_arabic_text(surah_number, ayah_number)
        english_text = surah["english_ayahs"][str(ayah_number)]
        surah_name = surah["english"]
    except (KeyError, TypeError) as exc:
        LOGGER.exception("Curated ayah %s is missing from Quran data.", reference)
        raise HTTPException(status_code=500, detail="Selected ayah is unavailable.") from exc

    images_dir = BACKGROUND_DIR / "images"
    background_paths = sorted(
        path for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ) if images_dir.is_dir() else []
    if not background_paths:
        raise HTTPException(status_code=503, detail="No image backgrounds are available.")
    background_path = random.choice(background_paths)

    with FEATURED_IMAGE_LOCK:
        last_render = LAST_IMAGE_RENDER.get(user["id"])
        if last_render is not None and time.monotonic() - last_render < IMAGE_COOLDOWN_SECONDS:
            raise HTTPException(status_code=429, detail="Please wait a few seconds before creating another image.")
        LAST_IMAGE_RENDER[user["id"]] = time.monotonic()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"ayah-image-{uuid4().hex}.png"
    image_path = OUTPUT_DIR / filename

    renderer.render_ayah_image(
        arabic_text,
        english_text,
        f"Surah {surah_name} {surah_number}:{ayah_number}",
        background_path,
        image_path,
    )
    if SUPABASE_SERVICE_KEY:
        _upload_media_file(image_path, f"ayah-images/{filename}", "image/png")
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    try:
        _record_image_history(user["id"], filename, reference)
    except Exception as history_error:
        LOGGER.error("Could not record ayah image history: %s", _redact_secrets(str(history_error)))
    return {
        "filename": filename,
        "reference": reference,
        "image_data": f"data:image/png;base64,{image_data}",
    }


# 6. API Endpoints
@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    # async: runs on the event loop, so it answers even while a render saturates the threadpool.
    return {"status": "online", "system": "Noor ul Quran Engine"}

@app.get("/api/reciters")
def list_reciters():
    return {
        "defaults": {
            "ar": DEFAULT_ARABIC_RECITER_ID,
            "ur": DEFAULT_URDU_VOICE_ID,
        },
        "reciters": [
            {
                "id": item["id"],
                "display_name": item["display_name"],
                "language": item["language"],
            }
            for item in RECITER_CONFIG["reciters"]
        ],
    }


def _validate_reciter_id(reciter_id, language, default_id):
    selected_id = reciter_id or default_id
    reciter = RECITERS_BY_ID.get(selected_id)
    if reciter is None or reciter["language"] != language:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown {'Arabic reciter' if language == 'ar' else 'Urdu voice'} id.",
        )
    return selected_id


# Background music ki files list karne ke liye endpoint:
def _available_music_filenames():
    music_dir = BACKGROUND_DIR / "music"
    music_dir.mkdir(parents=True, exist_ok=True)

    if SUPABASE_SERVICE_KEY:
        files = [name for name, _ in _list_bucket_files("music", MEDIA_BUCKET, MUSIC_FILE_EXTENSIONS)]
        if files:
            return sorted(set(files), key=str.lower)

    return sorted(
        [p.name for p in music_dir.iterdir() if p.is_file() and p.suffix.lower() in MUSIC_FILE_EXTENSIONS],
        key=lambda name: name.lower(),
    )


def _validate_music_filename(filename: str) -> str:
    if (
        not isinstance(filename, str)
        or not filename
        or filename.strip() != filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or Path(filename).is_absolute()
        or ntpath.isabs(filename)
        or filename not in _available_music_filenames()
    ):
        raise HTTPException(status_code=400, detail="Invalid music filename.")

    music_root = (BACKGROUND_DIR / "music").resolve()
    music_path = (music_root / filename).resolve()
    if not music_path.is_relative_to(music_root):
        raise HTTPException(status_code=400, detail="Invalid music filename.")
    return filename


@app.get("/api/music-list")
def list_background_music(user=Depends(get_current_user)):
    return {"music_files": _available_music_filenames()}


@app.get("/api/admin/music")
def list_admin_background_music(user=Depends(require_admin)):
    return {"music_files": _available_music_filenames()}


async def _receive_upload(request: Request, directory: Path, label: str) -> tuple[Path, int]:
    """Stream the request body to a temp file in `directory`, enforcing the 50 MB cap."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if re.fullmatch(r"[0-9]+", content_length) is None:
            raise HTTPException(status_code=400, detail="Invalid content length.")
        if int(content_length) > MAX_BACKGROUND_BYTES:
            raise HTTPException(status_code=413, detail=f"{label} must be 50 MB or smaller.")
    directory.mkdir(parents=True, exist_ok=True)
    temporary_path = directory / f".{uuid4().hex}.upload"
    total = 0
    try:
        with temporary_path.open("wb") as output_file:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BACKGROUND_BYTES:
                    raise HTTPException(status_code=413, detail=f"{label} must be 50 MB or smaller.")
                output_file.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail=f"The uploaded {label.lower()} is empty.")
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path, total


def _media_filename(filename: str, extensions) -> str:
    if (
        not MEDIA_NAME_PATTERN.fullmatch(filename)
        or ".." in filename
        or Path(filename).suffix.lower() not in extensions
    ):
        raise HTTPException(status_code=400, detail="Invalid filename or file type.")
    return filename


@app.post("/api/admin/music")
async def upload_background_music(request: Request, user=Depends(require_admin)):
    filename = _media_filename(request.query_params.get("filename", ""), MUSIC_FILE_EXTENSIONS)
    extension = Path(filename).suffix.lower()
    music_dir = MUSIC_CACHE_DIR if SUPABASE_SERVICE_KEY else BACKGROUND_DIR / "music"
    temporary_path, total = await _receive_upload(request, music_dir, "Music file")
    try:
        if SUPABASE_SERVICE_KEY:
            content_type = (
                "audio/mpeg"
                if extension in {".mp3", ".mpeg"}
                else ALLOWED_AUDIO_TYPES.get(extension, "application/octet-stream")
            )
            _upload_media_file(temporary_path, f"music/{filename}", content_type)
        temporary_path.replace(music_dir / filename)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return {"filename": filename, "size": total}


@app.delete("/api/admin/music/{filename}")
def delete_background_music(filename: str, user=Depends(require_admin)):
    _media_filename(filename, MUSIC_FILE_EXTENSIONS)
    if SUPABASE_SERVICE_KEY:
        _remove_bucket_object(MEDIA_BUCKET, f"music/{filename}")
    (MUSIC_CACHE_DIR / filename).unlink(missing_ok=True)
    return {"deleted": filename}


def _local_video_path(filename: str) -> Path:
    if not MEDIA_FILENAME_PATTERN.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid video filename.")
    output_root = OUTPUT_DIR.resolve()
    video_path = (output_root / filename).resolve()
    if not video_path.is_relative_to(output_root):
        raise HTTPException(status_code=400, detail="Invalid video filename.")
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found.")
    return video_path


def _empty_video_history():
    return {"users": {}}


def _read_video_history_unlocked():
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_video_history()
    if not isinstance(history, dict) or not isinstance(history.get("users"), dict):
        return _empty_video_history()
    return history


def _write_video_history_unlocked(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = HISTORY_FILE.with_suffix(HISTORY_FILE.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(HISTORY_FILE)
        _push_history()
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


HISTORY_OBJECT = "state/history.json"


def _push_history():
    """Mirror history.json to Supabase so Recent survives Render's ephemeral disk."""
    if not SUPABASE_SERVICE_KEY:
        return
    try:
        _upload_media_file(HISTORY_FILE, HISTORY_OBJECT, "application/json")
    except Exception as exc:
        LOGGER.error("Could not back up history to Supabase: %s", _redact_secrets(str(exc)))


def _pull_history():
    if not SUPABASE_SERVICE_KEY or HISTORY_FILE.exists():
        return
    try:
        _download_media_file(HISTORY_OBJECT, HISTORY_FILE)
    except Exception as exc:
        LOGGER.error("Could not restore history from Supabase: %s", _redact_secrets(str(exc)))


def _safe_history_video_path(filename):
    if not isinstance(filename, str) or not MEDIA_FILENAME_PATTERN.fullmatch(filename):
        return None
    output_root = OUTPUT_DIR.resolve()
    video_path = (output_root / filename).resolve()
    if not video_path.is_relative_to(output_root):
        return None
    return video_path


def _cleanup_orphaned_output_files(now=None):
    cutoff = (time.time() if now is None else now) - 24 * 60 * 60
    removed = 0
    if not OUTPUT_DIR.exists():
        return removed
    for path in OUTPUT_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".tmp", ".temp", ".part"}:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            LOGGER.exception("Could not clean old output file %s", path.name)
    return removed


def _today():
    return date.today().isoformat()


def _daily_remaining(user):
    if user["is_admin"]:
        return None
    with DAILY_USAGE_LOCK:
        day, count = DAILY_USAGE.get(user["id"], (None, 0))
    return _daily_limit(user) - (count if day == _today() else 0)


def _reserve_daily_slot(user, n=1):
    """Charge n videos to today's allowance; admins are exempt from the count only."""
    if user["is_admin"]:
        return
    limit = _daily_limit(user)
    with DAILY_USAGE_LOCK:
        day, count = DAILY_USAGE.get(user["id"], (None, 0))
        count = count if day == _today() else 0
        if count + n > limit:
            left = max(0, limit - count)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily limit of {limit} videos reached. It resets tomorrow."
                    if n == 1 else
                    f"This batch has {n} videos but only {left} of your {limit} daily videos are left. Untick some picks."
                ),
            )
        DAILY_USAGE[user["id"]] = (_today(), count + n)


def _refund_daily_slot(user_id, n=1):
    with DAILY_USAGE_LOCK:
        day, count = DAILY_USAGE.get(user_id, (None, 0))
        if day == _today() and count > 0:
            DAILY_USAGE[user_id] = (day, max(0, count - n))


def _refund_job_remaining(job):
    """Give back every slot this job was charged that has not been refunded yet (call with the jobs lock held)."""
    left = job.get("charged", 1) - job.get("refunded", 0)
    if left > 0 and not job.get("admin"):
        _refund_daily_slot(job["owner_id"], left)
    job["refunded"] = job.get("charged", 1)


def _average_render_seconds():
    return sum(RENDER_DURATIONS) / len(RENDER_DURATIONS) if RENDER_DURATIONS else DEFAULT_RENDER_SECONDS


def _render_rate():
    """Render seconds needed per second of finished video (recent average)."""
    return sum(RENDER_RATES) / len(RENDER_RATES) if RENDER_RATES else DEFAULT_RENDER_RATE


def _unfinished(job):
    """(videos, video-seconds) a job still has to render."""
    items = job.get("items")
    if not items:
        return 1, float(job.get("planned_seconds") or 30)
    todo = [i for i in items if i["status"] in {"queued", "rendering"}]
    return len(todo), sum(i.get("seconds") or 30 for i in todo)


def _queue_info(job_id):
    """(videos ahead of job_id, estimated wait in s) counting videos and their seconds, not jobs.

    Call with GENERATION_JOBS_LOCK held.
    """
    ahead_videos, ahead_seconds = 0, 0.0
    for other_id, other in GENERATION_JOBS.items():
        if other_id == job_id:
            break
        if other["status"] in {"queued", "processing"}:
            n, secs = _unfinished(other)
            ahead_videos, ahead_seconds = ahead_videos + n, ahead_seconds + secs
    _, own_seconds = _unfinished(GENERATION_JOBS.get(job_id, {}))
    return ahead_videos, round((ahead_seconds + own_seconds) * _render_rate())


def _list_all_media_objects(prefix):
    items, offset = [], 0
    while True:
        response = _storage_request(
            "POST",
            f"/object/list/{quote(MEDIA_BUCKET, safe='')}",
            json={"prefix": prefix, "limit": 100, "offset": offset},
        )
        page = response.json() if response.ok else []
        items.extend(item for item in page if item.get("id"))  # folders have no id
        if len(page) < 100:
            return items
        offset += 100


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def cleanup_old_media(now=None):
    """Delete videos/images older than CLEANUP_DAYS: storage objects (videos/ and ayah-images/ in the
    media bucket only), local files and history metadata. Returns counts."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=CLEANUP_DAYS)
    counts = {"storage": 0, "local": 0, "history": 0}

    if SUPABASE_SERVICE_KEY:
        stale = []
        for prefix in ("videos", "ayah-images"):
            for item in _list_all_media_objects(prefix):
                created = _parse_timestamp(item.get("created_at"))
                if created and created < cutoff:
                    stale.append(f"{prefix}/{item['name']}")
        for start in range(0, len(stale), 100):
            _delete_media_objects(stale[start:start + 100])
        counts["storage"] = len(stale)

    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        changed = False
        for user_id, entries in list(history["users"].items()):
            if not isinstance(entries, list):
                continue
            kept = []
            for entry in entries:
                created = _parse_timestamp(entry.get("created_at")) if isinstance(entry, dict) else None
                if created is None or created >= cutoff:
                    kept.append(entry)
                    continue
                counts["history"] += 1
                video_path = _safe_history_video_path(entry.get("filename"))
                if video_path is not None and video_path.is_file():
                    try:
                        video_path.unlink()
                        counts["local"] += 1
                    except OSError:
                        LOGGER.exception("Could not delete old media %s", entry.get("filename"))
            if len(kept) != len(entries):
                history["users"][user_id] = kept
                changed = True
        if changed:
            _write_video_history_unlocked(history)
    LOGGER.info(
        "Cleanup (> %s days): %d storage objects, %d local files, %d history entries removed.",
        CLEANUP_DAYS, counts["storage"], counts["local"], counts["history"],
    )
    return counts


def _cleanup_loop():
    while True:
        try:
            cleanup_old_media()
        except Exception as exc:
            LOGGER.error("Media cleanup failed: %s", _redact_secrets(str(exc)))
        time.sleep(CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
def cleanup_output_on_startup():
    global RAQM_AVAILABLE
    RAQM_AVAILABLE = bool(features.check("raqm"))
    if not RAQM_AVAILABLE:
        LOGGER.error(RAQM_REQUIRED_ERROR)
    _quran_data()
    _cleanup_orphaned_output_files()
    _pull_history()
    _pull_account_state()
    if os.getenv("DISABLE_CLEANUP_THREAD") != "1":
        threading.Thread(target=_cleanup_loop, name="media-cleanup", daemon=True).start()


def _require_raqm_for_generation(content_mode: str):
    if content_mode in {"full", "urdu_only", "arabic_only"} and not RAQM_AVAILABLE:
        LOGGER.error(
            "%s (content_mode=%s)",
            RAQM_REQUIRED_ERROR,
            content_mode,
        )
        raise HTTPException(status_code=503, detail=RAQM_REQUIRED_ERROR)


def _record_video_history(user_id, videos, content_mode, quality, styles):
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user_id, [])
        if not isinstance(entries, list):
            entries = []
        entries = [entry for entry in entries if isinstance(entry, dict)]
        new_entries = [
            {
                "id": uuid4().hex,
                "filename": video["filename"],
                "surah": video.get("surah"),
                "start_ayah": video.get("start_ayah"),
                "end_ayah": video.get("end_ayah"),
                "caption": video.get("caption", ""),
                "content_mode": content_mode,
                "quality": quality,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "downloaded_at": None,
                "download_count": 0,
                "styled": bool(styles),
                "object_path": video.get("object_path"),
            }
            for video in reversed(videos)
            if isinstance(video, dict) and isinstance(video.get("filename"), str)
        ]
        history["users"][user_id] = new_entries + entries
        _write_video_history_unlocked(history)
        return [entry["id"] for entry in reversed(new_entries)]


def _record_image_history(user_id, filename, reference):
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user_id, [])
        if not isinstance(entries, list):
            entries = []
        entry = {
            "id": uuid4().hex,
            "type": "image",
            "filename": filename,
            "reference": reference,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "downloaded_at": None,
            "download_count": 0,
        }
        history["users"][user_id] = [entry] + [item for item in entries if isinstance(item, dict)]
        _write_video_history_unlocked(history)


def _signed_media_path(filename: str, download: bool = False) -> str:
    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    params = {"exp": expires, "sig": _video_signature(filename, expires)}
    if download:
        params["dl"] = "1"
    return f"/api/video/{quote(filename, safe='')}?{urlencode(params)}"


def _user_owns_video(user_id, filename):
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user_id, [])
        if isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("filename") == filename
            for entry in entries
        ):
            return True

    with GENERATION_JOBS_LOCK:
        for job in GENERATION_JOBS.values():
            if job.get("owner_id") != user_id:
                continue
            manifest_path = Path(job["manifest_path"])
            batch_id = manifest_path.stem.removeprefix("batch-")
            if not batch_id or not filename.startswith(f"{batch_id}_"):
                continue
            videos = list(job.get("videos", []))
            videos.extend(_read_generation_manifest(manifest_path))
            if any(video.get("filename") == filename for video in videos):
                return True
    return False


def _video_signature(filename: str, expires: int) -> str:
    payload = f"{filename}\n{expires}".encode("utf-8")
    return hmac.new(VIDEO_URL_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@app.get("/api/video-url/{filename}")
def create_video_url(filename: str, user=Depends(get_current_user)):
    if not _user_owns_video(user["id"], filename):
        raise HTTPException(status_code=404, detail="Video not found.")
    _local_video_path(filename)
    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    signature = _video_signature(filename, expires)
    query = urlencode({"exp": expires, "sig": signature})
    return {"url": f"/api/video/{quote(filename, safe='')}?{query}", "expires_at": expires}


def _file_chunks(video_path: Path, start: int, remaining: int):
    with video_path.open("rb") as video_file:
        video_file.seek(start)
        while remaining:
            chunk = video_file.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/api/video/{filename}")
def serve_signed_video(
    filename: str,
    exp: Optional[str] = None,
    sig: Optional[str] = None,
    dl: Optional[str] = None,
    range_header: Optional[str] = Header(default=None, alias="Range"),
):
    if (
        not isinstance(exp, str)
        or re.fullmatch(r"[0-9]{1,12}", exp) is None
        or not isinstance(sig, str)
        or re.fullmatch(r"[0-9a-f]{64}", sig) is None
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired video link.")
    expires = int(exp)
    if expires <= int(time.time()) or not hmac.compare_digest(sig, _video_signature(filename, expires)):
        raise HTTPException(status_code=403, detail="Invalid or expired video link.")
    video_path = _local_video_path(filename)

    file_size = video_path.stat().st_size
    start, end = 0, max(0, file_size - 1)
    status_code = 200
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'{"attachment" if dl == "1" else "inline"}; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or (not match.group(1) and not match.group(2)) or file_size == 0:
            raise HTTPException(
                status_code=416,
                detail="Requested byte range is not satisfiable.",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) if last else file_size - 1
        else:
            suffix_length = int(last)
            if suffix_length <= 0:
                raise HTTPException(
                    status_code=416,
                    detail="Requested byte range is not satisfiable.",
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        if start >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Requested byte range is not satisfiable.",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = min(end, file_size - 1)
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    content_length = max(0, end - start + 1)
    headers["Content-Length"] = str(content_length)
    return StreamingResponse(
        _file_chunks(video_path, start, content_length),
        status_code=status_code,
        media_type="image/png" if video_path.suffix == ".png" else "video/mp4",
        headers=headers,
    )


@app.get("/api/history")
def get_video_history(user=Depends(get_current_user)):
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user["id"], [])
        if not isinstance(entries, list):
            entries = []
        response = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item.setdefault("type", "video")
            video_path = _safe_history_video_path(item.get("filename"))
            item["has_file"] = bool(video_path and video_path.is_file()) or bool(
                SUPABASE_SERVICE_KEY and item["type"] == "video" and item.get("object_path")
            )
            if item["type"] == "image" and item["has_file"]:
                item["thumb_url"] = _signed_media_path(item["filename"])
            response.append(item)
    return {"videos": response}


@app.get("/api/history/{entry_id}/url")
def get_history_video_url(
    entry_id: str,
    purpose: str = "play",
    user=Depends(get_current_user),
):
    if purpose not in {"play", "download"}:
        raise HTTPException(status_code=400, detail="Purpose must be play or download.")

    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user["id"], [])
        if not isinstance(entries, list):
            entries = []
        entry = next(
            (
                item for item in entries
                if isinstance(item, dict) and item.get("id") == entry_id
            ),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        filename = entry.get("filename")
        video_path = _safe_history_video_path(filename)
        stored_path = entry.get("object_path") if SUPABASE_SERVICE_KEY else None
        if (video_path is None or not video_path.is_file()) and not stored_path:
            raise HTTPException(status_code=410, detail="Video expire ho gayi.")
        if purpose == "download":
            entry["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            try:
                download_count = int(entry.get("download_count") or 0)
            except (TypeError, ValueError):
                download_count = 0
            entry["download_count"] = download_count + 1
            _write_video_history_unlocked(history)

    local = video_path is not None and video_path.is_file()
    url = _signed_media_path(filename, download=purpose == "download") if local else _signed_media_url(stored_path)
    return {"url": url, "expires_at": int(time.time()) + VIDEO_URL_TTL_SECONDS}


def _validate_history_ids(ids):
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids or len(unique_ids) > MAX_BULK_HISTORY_ITEMS or any(
        not isinstance(entry_id, str) or HISTORY_ID_PATTERN.fullmatch(entry_id) is None
        for entry_id in unique_ids
    ):
        raise HTTPException(status_code=400, detail=f"Send 1 to {MAX_BULK_HISTORY_ITEMS} valid item ids.")
    return unique_ids


def _owned_history_entries(user_id, ids):
    """Return the user's entries for ids, or 404 if any id is not theirs."""
    with HISTORY_LOCK:
        entries = _read_video_history_unlocked()["users"].get(user_id, [])
    by_id = {
        item.get("id"): item
        for item in (entries if isinstance(entries, list) else [])
        if isinstance(item, dict)
    }
    if any(entry_id not in by_id for entry_id in ids):
        raise HTTPException(status_code=404, detail="Video not found.")
    return [by_id[entry_id] for entry_id in ids]


def _delete_history_entries(user_id, ids):
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(user_id, [])
        if not isinstance(entries, list):
            entries = []
        wanted = set(ids)
        removed = [item for item in entries if isinstance(item, dict) and item.get("id") in wanted]
        if len(removed) != len(wanted):
            raise HTTPException(status_code=404, detail="Video not found.")
        history["users"][user_id] = [
            item for item in entries if not (isinstance(item, dict) and item.get("id") in wanted)
        ]
        _write_video_history_unlocked(history)
        remaining_object_paths = {
            item.get("object_path")
            for user_entries in history["users"].values()
            if isinstance(user_entries, list)
            for item in user_entries
            if isinstance(item, dict) and isinstance(item.get("object_path"), str)
        }
    for entry in removed:
        video_path = _safe_history_video_path(entry.get("filename"))
        if video_path is not None:
            try:
                video_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.exception("Could not delete history file %s", entry.get("filename"))
    orphaned = [
        entry.get("object_path") for entry in removed
        if entry.get("object_path") not in remaining_object_paths
    ]
    if orphaned:
        try:
            _delete_media_objects(orphaned)
        except Exception:
            LOGGER.exception("Could not delete history media object")
    return len(removed)


@app.delete("/api/history/{entry_id}")
def delete_history_video(entry_id: str, user=Depends(get_current_user)):
    _delete_history_entries(user["id"], [entry_id])
    return {"deleted": True}


@app.post("/api/history/bulk-delete")
def bulk_delete_history(req: HistoryBulkRequest, user=Depends(get_current_user)):
    ids = _validate_history_ids(req.ids)
    return {"deleted": _delete_history_entries(user["id"], ids)}


def _history_zip_signature(user_id, ids, expires):
    return _video_signature(f"history-zip\n{user_id}\n{','.join(ids)}", expires)


@app.post("/api/history/bulk-download-url")
def create_history_zip_url(req: HistoryBulkRequest, user=Depends(get_current_user)):
    ids = _validate_history_ids(req.ids)
    entries = _owned_history_entries(user["id"], ids)
    missing = [e.get("filename") for e in entries if not _video_available(e.get("filename"), e.get("object_path"))]
    if len(missing) == len(entries):
        raise HTTPException(status_code=410, detail="The selected videos are no longer available.")
    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    query = urlencode({
        "u": user["id"],
        "ids": ",".join(ids),
        "exp": expires,
        "sig": _history_zip_signature(user["id"], ids, expires),
    })
    return {"url": f"/api/history/zip?{query}", "item_count": len(ids) - len(missing),
            "missing": missing, "expires_at": expires}


class _ZipStreamSink:
    """Write-only, unseekable target, so zipfile streams entries with data descriptors."""

    def __init__(self):
        self._chunks = []

    def write(self, data):
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def drain(self):
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def _stream_zip(paths_by_name, temporary=(), missing=()):
    """Stream a ZIP; temporary files are deleted once added, missing names are listed in MISSING.txt."""
    sink = _ZipStreamSink()
    try:
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for arcname, path in paths_by_name.items():
                info = zipfile.ZipInfo.from_file(path, arcname)
                info.compress_type = zipfile.ZIP_STORED
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        yield sink.drain()
                if path in temporary:
                    path.unlink(missing_ok=True)
                yield sink.drain()
            if missing:
                archive.writestr("MISSING.txt", _missing_note(missing))
        yield sink.drain()
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


@app.get("/api/history/zip")
def download_history_zip(
    u: Optional[str] = None,
    ids: Optional[str] = None,
    exp: Optional[str] = None,
    sig: Optional[str] = None,
):
    if (
        not isinstance(u, str) or not isinstance(ids, str)
        or not isinstance(exp, str) or re.fullmatch(r"[0-9]{1,12}", exp) is None
        or not isinstance(sig, str) or re.fullmatch(r"[0-9a-f]{64}", sig) is None
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired download link.")
    id_list = _validate_history_ids(ids.split(","))
    expires = int(exp)
    if expires <= int(time.time()) or not hmac.compare_digest(
        sig, _history_zip_signature(u, id_list, expires)
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired download link.")
    paths_by_name, temporary, missing = {}, set(), []
    for entry in _owned_history_entries(u, id_list):
        source = _video_source(entry.get("filename"), entry.get("object_path"))
        if source is None:
            missing.append(str(entry.get("filename")))
            continue
        paths_by_name[Path(entry["filename"]).name] = source[0]
        if source[1]:
            temporary.add(source[0])
    if not paths_by_name:
        raise HTTPException(status_code=410, detail="The selected files have expired: " + ", ".join(missing))

    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(u, [])
        downloaded_at = datetime.now(timezone.utc).isoformat()
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("id") in id_list:
                entry["downloaded_at"] = downloaded_at
                try:
                    entry["download_count"] = int(entry.get("download_count") or 0) + 1
                except (TypeError, ValueError):
                    entry["download_count"] = 1
        try:
            _write_video_history_unlocked(history)
        except OSError:
            LOGGER.exception("Could not record history ZIP download")

    return StreamingResponse(
        _stream_zip(paths_by_name, temporary, missing),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="noor-ul-quran-{len(paths_by_name)}-items.zip"',
            "Cache-Control": "private, no-store",
            "X-Missing-Items": str(len(missing)),
        },
    )


@app.get("/api/access")
def get_access(user=Depends(get_current_user)):
    return {
        "user_id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "premium_access": user["premium_access"],
        "daily_limit": None if user["is_admin"] else _daily_limit(user),
        "daily_remaining": _daily_remaining(user),
        # The render cap applies to everyone, admins included (they skip only the daily count).
        "max_video_seconds": MAX_VIDEO_SECONDS,
    }


@app.get("/api/estimate")
def estimate_reel(
    surah: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    mode: Optional[str] = None,
    length_limit: Optional[str] = "60",
    reciter_id: Optional[str] = None,
    urdu_voice_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    try:
        surah_number = int(surah)
        start_ayah = int(start)
        end_ayah = int(end)
        selected_length = int(length_limit)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Surah, ayah range, and length are required.") from exc
    if mode not in {"full", "urdu_only", "arabic_only"}:
        raise HTTPException(status_code=400, detail="Mode must be full, urdu_only, or arabic_only.")
    reciter_id = _validate_reciter_id(
        reciter_id, "ar", DEFAULT_ARABIC_RECITER_ID
    )
    if mode == "arabic_only":
        urdu_voice_id = DEFAULT_URDU_VOICE_ID
    else:
        urdu_voice_id = _validate_reciter_id(
            urdu_voice_id, "ur", DEFAULT_URDU_VOICE_ID
        )
    if not 30 <= selected_length <= max(30, MAX_VIDEO_SECONDS):
        raise HTTPException(status_code=400, detail=f"Length must be between 30 and {max(30, MAX_VIDEO_SECONDS)} seconds.")
    _validate_ayah_range(surah_number, start_ayah, end_ayah)

    import make as renderer

    try:
        plan = renderer.plan_duration(
            surah_number,
            start_ayah,
            end_ayah,
            selected_length,
            mode,
            _quran_data(),
            reciter_id,
            urdu_voice_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail="Ayah audio durations could not be measured.") from exc

    return {
        "estimated_seconds": round(plan["estimated_seconds"], 6),
        "fits_up_to": plan["fits_up_to"],
        "tolerance_fits_up_to": plan["tolerance_fits_up_to"],
        "within_tolerance": plan["tolerance_fits_up_to"] >= end_ayah,
        "total_count": plan["total_count"],
        "range_seconds": round(plan["range_seconds"], 6),
    }


SUGGEST_TARGETS = (15, 30, 60)


def _suggest_from(surah, first, last, target, seconds_of):
    """Whole ayahs from `first`, longest run with total <= target. Returns (end, total) or None."""
    total, end = 0.0, None
    for ayah in range(first, last + 1):
        length = seconds_of(ayah)
        if total + length > target:
            break
        total, end = total + length, ayah
    return None if end is None else (end, total)


def _build_suggestions(surah, verse_count, from_ayah, is_admin, seconds_of):
    starts = [(1, "start")] + ([(from_ayah, "selected")] if 1 < from_ayah <= verse_count else [])
    out = []
    for target in SUGGEST_TARGETS:
        for first, origin in starts:
            last = min(verse_count, first + MAX_AYAHS_PER_REQUEST - 1)
            hit = _suggest_from(surah, first, last, target, seconds_of)
            item = {"target": target, "origin": origin, "allowed": True, "reason": None}
            if hit is None:
                item.update(start=None, end=None, seconds=None, allowed=False,
                            reason=f"one ayah is longer than {target} s",
                            label=f"{target} sec: one ayah is longer than {target} s")
            else:
                end, total = hit
                item.update(start=first, end=end, seconds=round(total, 1),
                            label=f"{target} sec: {surah}:{first}-{end} ({round(total)} s)")
                if target > MAX_VIDEO_SECONDS:
                    item.update(allowed=False, reason=f"Free server limit {MAX_VIDEO_SECONDS} s")
            out.append(item)
    return out


@app.get("/api/suggest-ranges")
def suggest_ranges(
    surah: Optional[str] = None,
    mode: Optional[str] = None,
    from_ayah: Optional[str] = None,
    reciter_id: Optional[str] = None,
    urdu_voice_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    try:
        surah_number = int(surah)
        selected_start = int(from_ayah) if from_ayah else 1
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Surah is required.") from exc
    if mode not in {"full", "urdu_only", "arabic_only"}:
        raise HTTPException(status_code=400, detail="Mode must be full, urdu_only, or arabic_only.")
    reciter_id = _validate_reciter_id(reciter_id, "ar", DEFAULT_ARABIC_RECITER_ID)
    urdu_voice_id = DEFAULT_URDU_VOICE_ID if mode == "arabic_only" else _validate_reciter_id(
        urdu_voice_id, "ur", DEFAULT_URDU_VOICE_ID
    )
    quran = _quran_data()
    if str(surah_number) not in quran["surahs"]:
        raise HTTPException(status_code=400, detail="Surah must be between 1 and 114.")
    verse_count = int(quran["surahs"][str(surah_number)]["n"])

    import make as renderer

    seconds = {}

    def seconds_of(ayah):
        if ayah not in seconds:
            plan = renderer.plan_duration(
                surah_number, ayah, ayah, 30, mode, quran, reciter_id, urdu_voice_id
            )
            seconds[ayah] = plan["items"][0]["len"]
        return seconds[ayah]

    try:
        suggestions = _build_suggestions(
            surah_number, verse_count, selected_start, user["is_admin"], seconds_of
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail="Ayah audio durations could not be measured.") from exc
    return {"suggestions": suggestions}


@app.get("/api/admin/diagnostics")
def admin_diagnostics(user=Depends(require_admin)):
    ffmpeg_version = "unavailable"
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.stdout:
            ffmpeg_version = result.stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        LOGGER.warning("Could not read the FFmpeg version for admin diagnostics")

    quran_file = BASE_DIR / "data" / "quran.json"
    ayat_count = 0
    if quran_file.is_file():
        quran = json.loads(quran_file.read_text(encoding="utf-8"))
        ayat_count = sum(
            int(surah["n"])
            for surah in quran.get("surahs", {}).values()
        )

    output_used_bytes = sum(
        path.stat().st_size
        for path in OUTPUT_DIR.rglob("*")
        if path.is_file()
    )
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        history_count = sum(
            sum(isinstance(entry, dict) for entry in entries)
            for entries in history["users"].values()
            if isinstance(entries, list)
        )

    disk = shutil.disk_usage(OUTPUT_DIR)
    return {
        "ffmpeg_version": ffmpeg_version,
        "raqm": bool(features.check("raqm")),
        "quran_json_present": quran_file.is_file(),
        "quran_ayat_count": ayat_count,
        "background_file_count": sum(
            1
            for path in BACKGROUND_DIR.iterdir()
            if path.is_file() and path.suffix.lower() == ".mp4"
        ),
        "music_file_count": len(_available_music_filenames()),
        "output_free_bytes": disk.free,
        "output_used_bytes": output_used_bytes,
        "history_entry_count": history_count,
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_anon_key_set": bool(SUPABASE_KEY),
        "supabase_service_role_key_set": bool(SUPABASE_SERVICE_KEY),
        "video_url_secret_set": bool(os.getenv("VIDEO_URL_SECRET")),
    }


@app.patch("/api/admin/users/{user_id}/premium")
def set_premium_access(user_id: UUID, req: PremiumAccessRequest, user=Depends(require_admin)):
    response = _supabase_request(
        "POST",
        "/rest/v1/rpc/noor_admin_set_premium",
        user["token"],
        json={"target_user_id": str(user_id), "enabled": req.enabled},
    )
    _raise_supabase_error(response)
    return {"user_id": str(user_id), "premium_access": req.enabled}


@app.get("/api/admin/backgrounds")
def list_backgrounds(user=Depends(require_admin)):
    if SUPABASE_SERVICE_KEY:
        return [
            {"filename": name, "size": size or 0}
            for name, size in _list_bucket_files("", BACKGROUNDS_BUCKET, {".mp4"})
        ]
    clips = {
        path.name: path
        for directory in (BACKGROUND_DIR, CLIP_CACHE_DIR)
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4"
    }
    return [{"filename": name, "size": clips[name].stat().st_size} for name in sorted(clips, key=str.lower)]


@app.post("/api/admin/backgrounds")
async def upload_background(request: Request, user=Depends(require_admin)):
    filename = _media_filename(request.query_params.get("filename", ""), {".mp4"})
    directory = CLIP_CACHE_DIR if SUPABASE_SERVICE_KEY else BACKGROUND_DIR
    temporary_path, total = await _receive_upload(request, directory, "Background clip")
    try:
        with temporary_path.open("rb") as clip:
            if clip.read(12)[4:8] != b"ftyp":
                raise HTTPException(status_code=400, detail="The file is not a valid MP4 video.")
        if SUPABASE_SERVICE_KEY:
            _upload_media_file(temporary_path, filename, "video/mp4", BACKGROUNDS_BUCKET)
        temporary_path.replace(directory / filename)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return {"filename": filename, "size": total}


@app.delete("/api/admin/backgrounds/{filename}")
def delete_background(filename: str, user=Depends(require_admin)):
    _media_filename(filename, {".mp4"})
    if SUPABASE_SERVICE_KEY:
        _remove_bucket_object(BACKGROUNDS_BUCKET, filename)
    (CLIP_CACHE_DIR / filename).unlink(missing_ok=True)
    return {"deleted": filename}


def _validate_verse(surah: int, ayah: int):
    if not 1 <= surah <= 114:
        raise HTTPException(status_code=400, detail="Surah must be between 1 and 114.")
    try:
        verse_count = int(_quran_data()["surahs"][str(surah)]["n"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Quran data could not be read.") from exc
    if not 1 <= ayah <= verse_count:
        raise HTTPException(status_code=400, detail=f"Ayah must be between 1 and {verse_count} for this surah.")


@app.api_route("/config.js", methods=["GET", "HEAD"], include_in_schema=False)
def frontend_config():
    config = {
        "url": SUPABASE_URL,
        "anonKey": SUPABASE_KEY,
        "apiBaseUrl": os.getenv("API_BASE_URL", "/api").rstrip("/"),
    }
    script = (
        f"window.NOOR_SUPABASE_CONFIG = {json.dumps({k: config[k] for k in ('url', 'anonKey')})};"
        f"window.NOOR_SUPABASE_CONFIG.apiBaseUrl = {json.dumps(config['apiBaseUrl'])};"
        f"window.NOOR_API_BASE_URL = {json.dumps(config['apiBaseUrl'])};"
        f"window.NOOR_CONTACT_EMAIL = {json.dumps(os.getenv('CONTACT_EMAIL', 'contact@example.com'))};"
    )
    return Response(
        content=script,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon():
    return FileResponse(PUBLIC_DIR / "assets" / "logo.png", media_type="image/png")


@app.get("/admin.html", include_in_schema=False)
def admin_page():
    return RedirectResponse(url="/admin-console.html")


@app.get("/admin-panel.html", include_in_schema=False)
def legacy_admin_page():
    return RedirectResponse(url="/admin-console.html")


@app.get("/auth", include_in_schema=False)
def auth_page():
    return RedirectResponse(url="/auth.html")


@app.get("/generate", include_in_schema=False)
def generate_page():
    return RedirectResponse(url="/generate.html")


@app.get("/signin", include_in_schema=False)
def signin_page():
    return RedirectResponse(url="/auth.html")


@app.get("/signin.html", include_in_schema=False)
def signin_html_page():
    return RedirectResponse(url="/auth.html")


def _over_cap_message(surah, first, plan, cap, name=None):
    """400 text for a range whose real length exceeds the cap, with the nearest range that fits."""
    prefix = f"{name}: " if name else ""
    text = f"{prefix}This range is {plan['range_seconds']:.1f} s; the server limit is {cap} s."
    total, last = 0.0, None
    for item in plan["items"]:
        if total + item["len"] > cap:
            break
        total, last = total + item["len"], item["ayah"]
    if last is None:
        return f"{text} Try {surah}:{first} on the local app."
    ref = f"{surah}:{first}" if last == first else f"{surah}:{first}-{last}"
    return f"{text} Try {ref} ({total:.1f} s)."


def _plan_or_http(surah, first, last, length_limit, content_mode, reciter_id, urdu_voice_id):
    import make as renderer

    try:
        return renderer.plan_duration(
            surah, first, last, length_limit, content_mode, _quran_data(), reciter_id, urdu_voice_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail="Ayah audio durations could not be measured.") from exc


def _new_items(refs):
    return [
        {"index": i, "pick_id": pid, "ref": ref, "seconds": seconds, "status": "queued",
         "reason": None, "history_id": None, "filename": None}
        for i, (pid, ref, seconds) in enumerate(refs, 1)
    ]


@app.post("/api/generate", status_code=202)
def generate_reel(req: GenerateRequest, user=Depends(get_current_user)):
    if req.mode not in {"pick", "picks", "random"}:
        raise HTTPException(status_code=400, detail="Invalid generation mode.")
    if req.mode != "pick" and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for custom and random generation.")
    cap = MAX_VIDEO_SECONDS
    content_mode = req.content_mode
    pick_rows = []
    if req.mode == "picks":
        # Refs come only from the server's pick list, never from the client.
        pick_rows = _resolve_pick_batch(req.pick_ids)
        content_mode = picks_lib.MODE_TO_CONTENT[pick_rows[0]["mode"]]
        if (req.reciter_id or DEFAULT_ARABIC_RECITER_ID) != DEFAULT_ARABIC_RECITER_ID or (
            req.urdu_voice_id or DEFAULT_URDU_VOICE_ID
        ) != DEFAULT_URDU_VOICE_ID:
            raise HTTPException(status_code=400, detail="Picks use the default reciter and Urdu voice.")
    if content_mode not in {"full", "urdu_only", "arabic_only"}:
        raise HTTPException(
            status_code=400,
            detail="Content mode must be full, urdu_only, or arabic_only.",
        )
    if content_mode == "arabic_only" and req.music:
        raise HTTPException(
            status_code=400,
            detail="Background music is not available in Arabic-only mode.",
        )
    reciter_id = _validate_reciter_id(
        req.reciter_id, "ar", DEFAULT_ARABIC_RECITER_ID
    )
    if content_mode == "arabic_only":
        urdu_voice_id = DEFAULT_URDU_VOICE_ID
    else:
        urdu_voice_id = _validate_reciter_id(
            req.urdu_voice_id, "ur", DEFAULT_URDU_VOICE_ID
        )
    music_filename = _validate_music_filename(req.music) if req.music is not None else None
    if req.mode == "pick":
        verse_match = re.fullmatch(
            r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?\s*",
            req.verse or "",
        )
        if not verse_match:
            raise HTTPException(status_code=400, detail="Enter a valid Surah:Ayah or Surah:Start-End range.")
        surah_number, start_ayah = int(verse_match[1]), int(verse_match[2])
        end_ayah = int(verse_match[3] or start_ayah)
        _validate_ayah_range(surah_number, start_ayah, end_ayah)
    if req.max_duration > 60 and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for videos longer than 60 seconds.")
    if req.random_count < 1 or req.random_count > 25:
        raise HTTPException(status_code=400, detail="Random count must be between 1 and 25.")
    # Picks ignore max_duration: each pick renders whole and is checked against the cap below.
    if req.mode != "picks" and (req.max_duration < 30 or req.max_duration > max(30, cap)):
        raise HTTPException(status_code=400, detail=f"Maximum duration must be between 30 and {max(30, cap)} seconds.")
    if req.quality not in RENDER_QUALITIES:
        raise HTTPException(status_code=400, detail="Quality must be fast, balanced, or high.")
    max_dur = cap if req.mode == "picks" else req.max_duration
    _check_render_memory(max_dur, req.quality)
    validate_video_styles(req.styles)
    _require_raqm_for_generation(content_mode)

    # Real-seconds guard: what would actually be rendered must fit the cap (admins included).
    refs = []
    if req.mode == "pick":
        plan = _plan_or_http(surah_number, start_ayah, end_ayah, req.max_duration, content_mode, reciter_id, urdu_voice_id)
        if plan["fit_seconds"] > cap:
            raise HTTPException(status_code=400, detail=_over_cap_message(
                surah_number, start_ayah, {**plan, "range_seconds": plan["fit_seconds"]}, cap))
        refs = [(None, req.verse.strip(), plan["fit_seconds"])]
        planned_seconds = plan["fit_seconds"]
    elif req.mode == "picks":
        for row in pick_rows:
            s, a0, a1 = picks_lib.split_ref(row["ref"])
            plan = _plan_or_http(s, a0, a1, cap, content_mode, reciter_id, urdu_voice_id)
            if plan["range_seconds"] > cap:
                raise HTTPException(status_code=400, detail=_over_cap_message(s, a0, plan, cap, name=row["ref"]))
            refs.append((row["id"], row["ref"], plan["range_seconds"]))
        planned_seconds = sum(r[2] for r in refs)
    else:
        refs = [(None, None, req.max_duration)] * req.random_count
        planned_seconds = req.max_duration * req.random_count

    cmd = [sys.executable, str(BASE_DIR / "make.py")]
    batch_id = uuid4().hex[:10]
    work_dir = CACHE_DIR / "tmp" / batch_id
    manifest_path = CACHE_DIR / f"batch-{batch_id}.json"
    if req.mode == "pick":
        cmd.extend(["--pick", req.verse])
    elif req.mode == "picks":
        cmd.extend(["--picks", str(work_dir / "picks.txt")])  # snapshot written below
    else:
        cmd.extend(["--random", str(req.random_count)])
    if content_mode == "urdu_only":
        cmd.append("--urdu-only")
    elif content_mode == "arabic_only":
        cmd.append("--arabic-only")
    cmd.extend(["--reciter", reciter_id])
    if content_mode != "arabic_only":
        cmd.extend(["--urdu-voice", urdu_voice_id])

    # Background music argument agar select kiya gaya ho (yeh ab bilkul theek jagah par hai):
    if music_filename is not None:
        cmd.extend(["--music", music_filename])

    if req.styles:
        style_path = work_dir / "style.json"
        cmd.extend(["--style-json", str(style_path)])
    cmd.extend([
        "--max-dur", str(max_dur),
        "--quality", req.quality,
        "--batch-id", batch_id,
        "--work-dir", str(work_dir),
        "--result-json", str(manifest_path),
    ])

    # Picks are charged per video; single and random jobs stay one slot each.
    charge = len(refs) if req.mode == "picks" else 1
    job_id = uuid4().hex
    with GENERATION_JOBS_LOCK:
        if sum(
            job["owner_id"] == user["id"] and job["status"] in {"queued", "processing"}
            for job in GENERATION_JOBS.values()
        ) >= MAX_ACTIVE_JOBS_PER_USER:
            raise HTTPException(
                status_code=429,
                detail="You already have a generation job queued or processing. Wait for it to finish before starting another.",
            )
        if len(GENERATION_JOBS) >= 64:
            for old_job_id, old_job in list(GENERATION_JOBS.items()):
                if old_job["status"] in {"completed", "partial", "failed"}:
                    GENERATION_JOBS.pop(old_job_id)
                if len(GENERATION_JOBS) < 64:
                    break
        if len(GENERATION_JOBS) >= 64:
            raise HTTPException(status_code=503, detail="Generation queue is full. Try again after a running reel finishes.")
        _reserve_daily_slot(user, charge)
        GENERATION_JOBS[job_id] = {
            "owner_id": user["id"],
            "admin": bool(user["is_admin"]),
            "status": "queued",
            "mode": req.mode,
            "quality": req.quality,
            "batch_id": batch_id,
            "error": None,
            "videos": [],
            "warnings": [],
            "items": _new_items(refs),
            "planned_seconds": planned_seconds,
            "charged": charge,
            "refunded": 0,
            "per_video_charge": req.mode == "picks",
            "manifest_path": str(manifest_path),
        }
        queue_ahead, wait_seconds = _queue_info(job_id)
    if req.mode == "picks":
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "picks.txt").write_text("\n".join(r[1] for r in refs) + "\n", encoding="utf-8")
    GENERATION_EXECUTOR.submit(
        _run_generation_job,
        job_id,
        cmd,
        req.mode,
        manifest_path,
        work_dir,
        req.styles,
        user["id"],
        content_mode,
        req.quality,
        RENDER_TIMEOUT_SECONDS * len(refs),
    )
    return {
        "job_id": job_id,
        "batch_id": batch_id,
        "status": "queued",
        "message": "Generation queued. Each reel will appear here as soon as it is ready.",
        "videos": [],
        "items": GENERATION_JOBS[job_id]["items"],
        "queue_ahead": queue_ahead,
        "estimated_wait_seconds": wait_seconds,
        "daily_remaining": _daily_remaining(user),
    }


def _read_manifest(manifest_path: Path):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [e for e in manifest if isinstance(e, dict)] if isinstance(manifest, list) else []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []


def _read_generation_manifest(manifest_path: Path):
    """Finished reels only; failed reels are {index, error} entries without a filename."""
    return [e for e in _read_manifest(manifest_path) if isinstance(e.get("filename"), str)]


def _kill_process_tree(process):
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()


def _cgroup_memory_bytes():
    """Container memory in use (anon + page cache, what the OOM killer sees); None if not in a cgroup."""
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            return int(Path(path).read_text())
        except (OSError, ValueError):
            continue
    return None


def _watch_peak_memory(done, peak):
    while not done.wait(0.25):
        used = _cgroup_memory_bytes()
        if used:
            peak[0] = max(peak[0], used)


def _run_render(cmd, timeout, job_id=None, on_tick=None):
    """Run make.py; on timeout or cancel kill it and every FFmpeg child it started.

    on_tick() runs about once a second while it renders, so finished reels can be handled early.
    """
    group_args = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt" else {"start_new_session": True}
    )
    opts = {cmd[i]: cmd[i + 1] for i in range(len(cmd) - 1) if cmd[i] in ("--quality", "--max-dur")}
    started = time.monotonic()
    done, peak = threading.Event(), [_cgroup_memory_bytes() or 0]
    threading.Thread(target=_watch_peak_memory, args=(done, peak), daemon=True).start()
    LOGGER.info(
        "render start quality=%s max_dur=%s profile=%s mem_now=%sMiB",
        opts.get("--quality"), opts.get("--max-dur"), os.getenv("RENDER_MEMORY_PROFILE", "default"),
        peak[0] >> 20,
    )
    process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "MAX_VIDEO_SECONDS": str(MAX_VIDEO_SECONDS)},
        **group_args,
    )
    if job_id:
        with GENERATION_JOBS_LOCK:
            if job_id in GENERATION_JOBS:
                GENERATION_JOBS[job_id]["_proc"] = process
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if job_id and _job_cancelled(job_id):
                    _kill_process_tree(process)
                    process.communicate()
                    raise RuntimeError("Generation cancelled because the account is unavailable.")
                if time.monotonic() - started > timeout:
                    _kill_process_tree(process)
                    process.communicate()
                    raise RuntimeError(f"Rendering took longer than {timeout} seconds and was stopped.")
                if on_tick:
                    on_tick()
    finally:
        done.set()
        if job_id:
            with GENERATION_JOBS_LOCK:
                GENERATION_JOBS.get(job_id, {}).pop("_proc", None)
        # rc -9 / SIGKILL with no error text is how an OOM kill looks from here.
        LOGGER.info(
            "render end quality=%s max_dur=%s rc=%s secs=%.0f peak_mem=%sMiB",
            opts.get("--quality"), opts.get("--max-dur"), process.returncode,
            time.monotonic() - started, peak[0] >> 20,
        )
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def _run_generation_job(
    job_id,
    cmd,
    mode,
    manifest_path,
    work_dir,
    styles=None,
    user_id=None,
    content_mode="full",
    quality="high",
    timeout=RENDER_TIMEOUT_SECONDS,
):
    global EPHEMERAL_MEDIA_READY
    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if job and job.get("cancelled"):
            return
        if job:
            job["status"] = "processing"
    started = time.monotonic()
    handled = set()  # manifest positions already uploaded + recorded

    def settle(entries, final=False, default_reason=None):
        """Upload and record every newly finished reel, mark failed ones, refund their slots."""
        done_videos = [e for e in entries if isinstance(e.get("filename"), str)]
        for n, v in enumerate(done_videos, 1):
            v.setdefault("index", n)
        failures = {e.get("index"): e.get("error") for e in entries if "filename" not in e}
        for position, video in enumerate(done_videos):
            if position in handled:
                continue
            handled.add(position)
            warning = None
            if SUPABASE_SERVICE_KEY:
                try:
                    stored_video = f"videos/{uuid4().hex}_{video['filename']}"
                    _upload_media_file(OUTPUT_DIR / video["filename"], stored_video, "video/mp4")
                    video["object_path"] = stored_video
                    video["url"] = _signed_media_url(stored_video)
                except Exception as exc:
                    warning = "Cloud copy failed; download this reel soon."
                    LOGGER.error("Upload failed (job_id=%s): %s", job_id, _redact_secrets(str(exc)))
            history_id = None
            if user_id is not None:
                try:
                    history_id = _record_video_history(user_id, [video], content_mode, quality, styles)[0]
                except Exception as history_error:
                    LOGGER.error(
                        "Could not update generated-video history (job_id=%s, user_id=%s): %s",
                        job_id, user_id, _redact_secrets(str(history_error)),
                    )
            video["history_id"] = history_id
            with GENERATION_JOBS_LOCK:
                job = GENERATION_JOBS.get(job_id)
                if not job:
                    continue
                job["videos"] = [v for v in job.get("videos", []) if v.get("filename") != video["filename"]] + [video]
                item = _item_for(job, video.get("index") or position + 1)
                if item:
                    item.update(status="done", filename=video["filename"], history_id=history_id,
                                batch_id=job.get("batch_id"), warning=warning)
        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if not job:
                return
            for item in job.get("items", []):
                if item["status"] in {"queued", "rendering"} and (
                    item["index"] in failures or (final and item["index"] not in {v.get("index") for v in done_videos})
                ):
                    reason = failures.get(item["index"]) or default_reason or "The renderer did not produce this reel."
                    item.update(status="failed", reason=_redact_secrets(str(reason))[:300])
                    if job.get("per_video_charge") and job["refunded"] < job["charged"]:
                        job["refunded"] += 1
                        if not job.get("admin"):
                            _refund_daily_slot(job["owner_id"])
            waiting = [i for i in job.get("items", []) if i["status"] == "queued"]
            if waiting and not final:
                waiting[0]["status"] = "rendering"

    try:
        with GENERATION_LOCK:
            if styles:
                work_dir.mkdir(parents=True, exist_ok=True)
                (work_dir / "style.json").write_text(
                    json.dumps(styles, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if not EPHEMERAL_MEDIA_READY:
                _prepare_ephemeral_media()
                EPHEMERAL_MEDIA_READY = True
            _sync_render_media(cmd[cmd.index("--music") + 1] if "--music" in cmd else None)
            _cleanup_orphaned_output_files()
            with GENERATION_JOBS_LOCK:
                job = GENERATION_JOBS.get(job_id)
                if job and job.get("items"):
                    job["items"][0]["status"] = "rendering"
            completed = _run_render(
                cmd, timeout, job_id=job_id, on_tick=lambda: settle(_read_manifest(manifest_path)),
            )
            entries = _read_manifest(manifest_path)
            videos = [e for e in entries if isinstance(e.get("filename"), str)]
            if _job_cancelled(job_id):
                for video in videos:
                    (OUTPUT_DIR / video["filename"]).unlink(missing_ok=True)
                raise RuntimeError("Generation cancelled because the account is unavailable.")
            if completed.returncode != 0 and not videos:
                raise RuntimeError(completed.stderr or completed.stdout or "Video rendering failed.")
            if not videos:
                raise RuntimeError("No video output was generated.")
            tail = (completed.stderr or "").strip().splitlines()
            settle(entries, final=True, default_reason=tail[-1] if completed.returncode != 0 and tail else None)

            if SUPABASE_SERVICE_KEY:
                captions_path = OUTPUT_DIR / "captions.csv"
                used_path = CACHE_DIR / "used.json"
                try:
                    if captions_path.exists():
                        _upload_media_file(captions_path, "metadata/captions.csv", "text/csv")
                    if used_path.exists():
                        _upload_media_file(used_path, "state/used.json", "application/json")
                except Exception as exc:
                    LOGGER.error("Could not back up captions/used state: %s", _redact_secrets(str(exc)))

        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                skipped = [i for i in job.get("items", []) if i["status"] == "failed"]
                job["warnings"] = list(dict.fromkeys(
                    warning for video in job["videos"] for warning in video.get("warnings", [])
                ))
                clean = completed.returncode == 0 and not skipped
                job["status"] = "completed" if clean else "partial"
                job["message"] = (
                    "All reels are ready." if clean else
                    f"{len(skipped)} reel(s) could not be rendered and were refunded; the rest are ready."
                    if skipped else "Some reels could not be rendered; completed reels are ready."
                )
        seconds = sum(v.get("duration") or 0 for v in videos)
        RENDER_DURATIONS.append((time.monotonic() - started) / max(1, len(videos)))
        del RENDER_DURATIONS[:-10]
        if seconds:
            RENDER_RATES.append((time.monotonic() - started) / seconds)
            del RENDER_RATES[:-10]
    except Exception as exc:
        safe_error = _redact_secrets(str(exc))
        LOGGER.error(
            "Generation job failed (job_id=%s, user_id=%s): %s",
            job_id,
            user_id,
            safe_error,
        )
        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                _refund_job_remaining(job)
                job["status"] = "failed"
                job["error"] = safe_error
                job["videos"] = [v for v in job["videos"]] or _read_generation_manifest(manifest_path)
                for item in job.get("items", []):
                    if item["status"] in {"queued", "rendering"}:
                        item.update(status="failed", reason=safe_error[:300])
            elif user_id is not None:
                _refund_daily_slot(user_id)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        manifest_path.with_suffix(manifest_path.suffix + ".tmp").unlink(missing_ok=True)


def _item_for(job, index):
    return next((i for i in job.get("items", []) if i["index"] == index), None)


@app.get("/api/generate/{job_id}")
def generation_status(job_id: str, user=Depends(get_current_user)):
    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if not job or job["owner_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Generation job not found.")
        response = {
            key: (list(value) if isinstance(value, list) else value) for key, value in job.items()
            if key not in {"owner_id", "manifest_path", "_proc", "admin", "charged", "refunded", "per_video_charge"}
        }
        if job["status"] in {"queued", "processing"}:
            response["queue_ahead"], response["estimated_wait_seconds"] = _queue_info(job_id)
        manifest_path = Path(job["manifest_path"])

    if response["status"] in {"queued", "processing"} and not response.get("videos"):
        response["videos"] = _read_generation_manifest(manifest_path)

    response["warnings"] = list(dict.fromkeys(
        warning
        for video in response["videos"]
        for warning in video.get("warnings", [])
    )) or response.get("warnings", [])
    response["job_id"] = job_id
    return response


@app.get("/api/generate/{job_id}/download-url")
def create_batch_download_url(job_id: str, user=Depends(get_current_user)):
    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if not job or job["owner_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Generation batch not found.")
        if job["status"] in {"queued", "processing"}:
            raise HTTPException(status_code=409, detail="Wait for the batch to finish before downloading it.")
        videos = list(job.get("videos", []))
        owner_id = job["owner_id"]
        if not videos:
            raise HTTPException(status_code=404, detail="This batch has no completed reels to download.")

    filenames, missing = [], []
    for video in videos:
        filename = video.get("filename") if isinstance(video, dict) else None
        if not isinstance(filename, str):
            raise HTTPException(status_code=500, detail="The batch contains an invalid video entry.")
        (filenames if _video_available(filename, video.get("object_path")) else missing).append(filename)
    if not filenames:
        raise HTTPException(status_code=404, detail="The reels in this batch are no longer available.")

    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    archive_name = f"batch-{job_id}.zip"
    signature = _video_signature(archive_name, expires)
    query = urlencode({"exp": expires, "sig": signature})
    return {
        "url": f"/api/batch-download/{quote(job_id, safe='')}?{query}",
        "filename": f"noor-ul-quran-reels-{job_id[:10]}.zip",
        "video_count": len(filenames),
        "missing": missing,
        "expires_at": expires,
    }


def _remove_temporary_archive(archive_path: Path):
    try:
        archive_path.unlink(missing_ok=True)
    except OSError:
        LOGGER.exception("Could not remove temporary reel archive %s", archive_path.name)


@app.get("/api/batch-download/{job_id}")
def download_generation_batch(job_id: str, exp: Optional[str] = None, sig: Optional[str] = None):
    if (
        not isinstance(exp, str)
        or re.fullmatch(r"[0-9]{1,12}", exp) is None
        or not isinstance(sig, str)
        or re.fullmatch(r"[0-9a-f]{64}", sig) is None
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired batch download link.")
    expires = int(exp)
    archive_name = f"batch-{job_id}.zip"
    if expires <= int(time.time()) or not hmac.compare_digest(
        sig,
        _video_signature(archive_name, expires),
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired batch download link.")

    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if not job or job["status"] in {"queued", "processing"}:
            raise HTTPException(status_code=404, detail="Generation batch not found or not finished.")
        videos = list(job.get("videos", []))
        owner_id = job["owner_id"]

    filenames, video_paths, temporary, missing = [], [], [], []
    for video in videos:
        filename = video.get("filename") if isinstance(video, dict) else None
        if not isinstance(filename, str):
            raise HTTPException(status_code=500, detail="The batch contains an invalid video entry.")
        source = _video_source(filename, video.get("object_path"))
        if source is None:
            missing.append(filename)
            continue
        video_paths.append(source[0])
        filenames.append(filename)
        if source[1]:
            temporary.append(source[0])
    if not video_paths:
        raise HTTPException(status_code=404, detail="This batch has no available reels to download.")

    archive_dir = CACHE_DIR / "tmp"
    archive_dir.mkdir(parents=True, exist_ok=True)
    temporary_file = tempfile.NamedTemporaryFile(
        prefix="noor-reels-",
        suffix=".zip",
        dir=archive_dir,
        delete=False,
    )
    archive_path = Path(temporary_file.name)
    temporary_file.close()
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for filename, video_path in zip(filenames, video_paths):
                archive.write(video_path, arcname=filename)
            if missing:
                archive.writestr("MISSING.txt", _missing_note(missing))
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        _remove_temporary_archive(archive_path)
        LOGGER.exception("Could not create reel archive for generation batch %s", job_id)
        raise HTTPException(status_code=500, detail="Could not create the ZIP download. Please try again.") from exc

    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        entries = history["users"].get(owner_id, [])
        if isinstance(entries, list):
            downloaded_at = datetime.now(timezone.utc).isoformat()
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("filename") not in filenames:
                    continue
                entry["downloaded_at"] = downloaded_at
                try:
                    entry["download_count"] = int(entry.get("download_count") or 0) + 1
                except (TypeError, ValueError):
                    entry["download_count"] = 1
            try:
                _write_video_history_unlocked(history)
            except OSError:
                LOGGER.exception("Could not record bulk download for generation batch %s", job_id)

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"noor-ul-quran-reels-{job_id[:10]}.zip",
        background=BackgroundTask(_remove_temporary_archive, archive_path),
    )


# ---------------------------------------------------------------------------------------------
# Managed Picks: admin-curated ayah lists per bucket (30/60/120 s) x mode (urdu/arabic).
# Stored in state/picks.json (Supabase, local copy beside the output dir), seeded from
# data/picks_default.json. Writes fail loudly: if Supabase rejects the save, nothing changes.
# ---------------------------------------------------------------------------------------------
PICKS_OBJECT = "state/picks.json"
PICKS_DEFAULT_FILE = BASE_DIR / "data" / "picks_default.json"
PICKS_LOCK = Lock()
_PICKS_PULLED = False
RECOMPUTE = {"state": "idle", "done": 0, "total": 0, "errors": [], "error": None}
RECOMPUTE_LOCK = Lock()


def _picks_file():
    return OUTPUT_DIR / "picks.json"


def _load_picks():
    global _PICKS_PULLED
    path = _picks_file()
    if SUPABASE_SERVICE_KEY and not _PICKS_PULLED:
        _PICKS_PULLED = True
        try:
            _download_media_file(PICKS_OBJECT, path)  # Supabase is the source of truth when it has a copy
        except Exception as exc:
            LOGGER.error("Could not restore %s: %s", PICKS_OBJECT, _redact_secrets(str(exc)))
    for source in (path, PICKS_DEFAULT_FILE):
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            return [p for p in data if isinstance(p, dict)]
    return []


def _save_picks(picks):
    """Persist to Supabase first (when configured), then locally. Raises 502 so the admin sees a failure."""
    path = _picks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(picks, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        if SUPABASE_SERVICE_KEY:
            _upload_media_file(temp, PICKS_OBJECT, "application/json")
    except Exception as exc:
        temp.unlink(missing_ok=True)
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not save picks to Supabase, nothing was changed: {_redact_secrets(str(detail))}",
        ) from exc
    temp.replace(path)


def _pick_seconds(mode, surah, start, end, measure=True):
    """Real seconds for the default reciter / Urdu voice; None if unknown and measure is False."""
    import make as renderer

    cached = picks_lib.seconds_lookup(renderer.load_duration_cache())(mode, surah, start, end)
    if cached is not None or not measure:
        return cached
    plan = _plan_or_http(surah, start, end, 10_000, picks_lib.MODE_TO_CONTENT[mode], None, None)
    return round(plan["range_seconds"], 2)


def _pick_view(pick, quran, cap):
    surah = picks_lib.split_ref(pick["ref"])[0]
    seconds = pick.get("seconds")
    return {
        **pick,
        "surah": surah,
        "surah_name": quran["surahs"][str(surah)]["english"],
        "selectable": seconds is not None and seconds <= cap,
        "over_bucket": seconds is not None and seconds > pick["bucket"],
    }


def _check_bucket_mode(bucket, mode):
    if bucket not in picks_lib.BUCKETS:
        raise HTTPException(status_code=400, detail="Bucket must be 30, 60 or 120.")
    if mode not in picks_lib.MODES:
        raise HTTPException(status_code=400, detail="Mode must be urdu or arabic.")


def _check_ref(ref):
    try:
        return picks_lib.parse_ref(ref, _quran_data())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_pick_batch(pick_ids):
    """Snapshot of the picks behind pick_ids: one bucket x mode, at most 25, no repeats."""
    if not pick_ids:
        raise HTTPException(status_code=400, detail="Choose at least one pick (pick_ids).")
    if len(pick_ids) > 25 or len(set(pick_ids)) != len(pick_ids):
        raise HTTPException(status_code=400, detail="Choose up to 25 different picks.")
    by_id = {p["id"]: p for p in _load_picks()}
    rows = []
    for pick_id in pick_ids:
        if pick_id not in by_id:
            raise HTTPException(status_code=400, detail=f"Unknown pick: {pick_id}")
        rows.append(dict(by_id[pick_id]))
    if len({(r["bucket"], r["mode"]) for r in rows}) != 1:
        raise HTTPException(status_code=400, detail="Each batch must use one length tab and one Urdu/Arabic mode.")
    return rows


class PickBody(BaseModel):
    bucket: Optional[int] = None
    mode: Optional[str] = None
    ref: Optional[str] = Field(default=None, max_length=16)


class PickImport(BaseModel):
    text: str = Field(max_length=200_000)
    bucket: int
    mode: Optional[str] = None
    apply: bool = False


class PickTrim(BaseModel):
    to_ayah: Optional[int] = None
    confirm: bool = False


class RecomputeRequest(BaseModel):
    remeasure: bool = False


def _pick_audit(user, pick_id, action, detail):
    _audit(user, pick_id, "", f"picks.{action}", detail)


@app.get("/api/picks")
def list_picks(bucket: Optional[int] = None, mode: Optional[str] = None, user=Depends(get_current_user)):
    quran, cap = _quran_data(), MAX_VIDEO_SECONDS
    rows = [
        _pick_view(p, quran, cap) for p in _load_picks()
        if (bucket is None or p["bucket"] == bucket) and (mode is None or p["mode"] == mode)
    ]
    return {
        "picks": rows,
        "cap": cap,
        "counts": {"total": len(rows), "over_bucket": sum(r["over_bucket"] for r in rows),
                   "over_cap": sum(not r["selectable"] for r in rows)},
    }


@app.post("/api/picks", status_code=201)
def add_pick(body: PickBody, user=Depends(require_admin)):
    _check_bucket_mode(body.bucket, body.mode)
    ref, s, a, b = _check_ref(body.ref)
    seconds = _pick_seconds(body.mode, s, a, b)
    with PICKS_LOCK:
        picks = _load_picks()
        if any(p["bucket"] == body.bucket and p["mode"] == body.mode and p["ref"] == ref for p in picks):
            raise HTTPException(status_code=409, detail=f"{ref} is already in this list.")
        pick = {"id": f"{body.bucket}{body.mode[0]}-{uuid4().hex[:6]}", "bucket": body.bucket, "mode": body.mode,
                "ref": ref, "seconds": seconds, "over_bucket": seconds > body.bucket}
        _save_picks(picks + [pick])
    _pick_audit(user, pick["id"], "add", f"{body.bucket}s {body.mode} {ref}")
    return _pick_view(pick, _quran_data(), MAX_VIDEO_SECONDS)


@app.put("/api/picks/{pick_id}")
def edit_pick(pick_id: str, body: PickBody, user=Depends(require_admin)):
    with PICKS_LOCK:
        picks = _load_picks()
        pick = next((p for p in picks if p["id"] == pick_id), None)
        if pick is None:
            raise HTTPException(status_code=404, detail="Pick not found.")
        bucket = body.bucket if body.bucket is not None else pick["bucket"]
        mode = body.mode or pick["mode"]
        _check_bucket_mode(bucket, mode)
        ref, s, a, b = _check_ref(body.ref or pick["ref"])
        if any(p is not pick and p["bucket"] == bucket and p["mode"] == mode and p["ref"] == ref for p in picks):
            raise HTTPException(status_code=409, detail=f"{ref} is already in this list.")
        seconds = _pick_seconds(mode, s, a, b)
        updated = {**pick, "bucket": bucket, "mode": mode, "ref": ref, "seconds": seconds,
                   "over_bucket": seconds > bucket}
        _save_picks([updated if p is pick else p for p in picks])
    _pick_audit(user, pick_id, "edit", f"{pick['ref']} -> {bucket}s {mode} {ref}")
    return _pick_view(updated, _quran_data(), MAX_VIDEO_SECONDS)


@app.delete("/api/picks/{pick_id}")
def delete_pick(pick_id: str, user=Depends(require_admin)):
    with PICKS_LOCK:
        picks = _load_picks()
        pick = next((p for p in picks if p["id"] == pick_id), None)
        if pick is None:
            raise HTTPException(status_code=404, detail="Pick not found.")
        _save_picks([p for p in picks if p is not pick])
    _pick_audit(user, pick_id, "delete", f"{pick['bucket']}s {pick['mode']} {pick['ref']}")
    return {"deleted": pick_id}


@app.post("/api/picks/import")
def import_picks(body: PickImport, user=Depends(require_admin)):
    if body.mode is not None or body.bucket is not None:
        _check_bucket_mode(body.bucket, body.mode or "urdu")
    found, invalid = picks_lib.parse_import(body.text, _quran_data(), body.mode)
    with PICKS_LOCK:
        picks = _load_picks()
        have = {(p["bucket"], p["mode"], p["ref"]) for p in picks}
        add, dups = [], []
        for mode, ref in found:
            key = (body.bucket, mode, ref)
            (dups if key in have else add).append({"mode": mode, "ref": ref})
            have.add(key)
        if body.apply and add:
            import make as renderer

            seconds_of = picks_lib.seconds_lookup(renderer.load_duration_cache())
            new = [
                picks_lib.make_pick(f"{body.bucket}{x['mode'][0]}-{uuid4().hex[:6]}", body.bucket, x["mode"], x["ref"], seconds_of)
                for x in add
            ]
            _save_picks(picks + new)
    if body.apply and add:
        _pick_audit(user, "import", "import", f"{len(add)} added to {body.bucket}s, {len(invalid)} invalid")
    return {"add": add, "duplicates": dups, "invalid": invalid, "applied": bool(body.apply and add)}


@app.get("/api/picks/export")
def export_picks(bucket: int, user=Depends(require_admin)):
    _check_bucket_mode(bucket, "urdu")
    rows = [p for p in _load_picks() if p["bucket"] == bucket]
    return Response(
        picks_lib.format_export(rows, _quran_data()),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="picks-{bucket}s.txt"'},
    )


def _recompute_worker(remeasure):
    import make as renderer

    state = RECOMPUTE
    try:
        picks = _load_picks()
        ayat = sorted({
            (p["mode"], s, n)
            for p in picks for s, a, b in [picks_lib.split_ref(p["ref"])] for n in range(a, b + 1)
        })
        with RECOMPUTE_LOCK:
            state.update(total=len(ayat), done=0)

        def one(key):
            mode, s, n = key
            kind = "arabic" if mode == "arabic" else "urdu"
            try:
                if remeasure:
                    renderer.forget_ayah_duration(s, n, kind)
                if renderer.cached_ayah_duration(s, n, kind) is None and renderer.measure_ayah_audio(s, n, kind) is None:
                    raise RuntimeError("audio unavailable")
            except Exception as exc:
                with RECOMPUTE_LOCK:
                    state["errors"].append(f"{s}:{n} ({mode}): {_redact_secrets(str(exc))[:120]}")
            with RECOMPUTE_LOCK:
                state["done"] += 1

        with renderer.batch_duration_writes(), ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, ayat))
        with PICKS_LOCK:
            fresh = _load_picks()
            seconds_of = picks_lib.seconds_lookup(renderer.load_duration_cache())
            for p in fresh:
                s, a, b = picks_lib.split_ref(p["ref"])
                seconds = seconds_of(p["mode"], s, a, b)
                p["seconds"] = seconds
                p["over_bucket"] = seconds is not None and seconds > p["bucket"]
            _save_picks(fresh)
        state["state"] = "done"
    except Exception as exc:
        state.update(state="failed", error=_redact_secrets(str(exc.detail if isinstance(exc, HTTPException) else exc)))


@app.post("/api/picks/recompute", status_code=202)
def recompute_picks(body: RecomputeRequest = RecomputeRequest(), user=Depends(require_admin)):
    with RECOMPUTE_LOCK:
        if RECOMPUTE["state"] == "running":
            raise HTTPException(status_code=409, detail="A recompute is already running.")
        RECOMPUTE.update(state="running", done=0, total=0, errors=[], error=None)
    threading.Thread(target=_recompute_worker, args=(body.remeasure,), daemon=True).start()
    _pick_audit(user, "all", "recompute", "remeasure" if body.remeasure else "fill missing")
    return dict(RECOMPUTE)


@app.get("/api/picks/recompute")
def recompute_status(user=Depends(require_admin)):
    with RECOMPUTE_LOCK:
        return dict(RECOMPUTE, errors=list(RECOMPUTE["errors"])[:20])


@app.post("/api/picks/{pick_id}/trim")
def trim_pick(pick_id: str, body: PickTrim, user=Depends(require_admin)):
    """Whole-ayah trim of an over-length pick into a NEW pick; the original stays. Needs confirm=true."""
    with PICKS_LOCK:
        picks = _load_picks()
        pick = next((p for p in picks if p["id"] == pick_id), None)
        if pick is None:
            raise HTTPException(status_code=404, detail="Pick not found.")
        s, a, b = picks_lib.split_ref(pick["ref"])
        if a == b:
            raise HTTPException(status_code=400, detail="A single ayah cannot be trimmed.")
        content = picks_lib.MODE_TO_CONTENT[pick["mode"]]
        plan = _plan_or_http(s, a, b, 10_000, content, None, None)
        if body.to_ayah is not None:
            if not a <= body.to_ayah < b:
                raise HTTPException(status_code=400, detail=f"Last ayah must be between {a} and {b - 1}.")
            last = body.to_ayah
        else:
            total, last = 0.0, None
            for item in plan["items"]:
                if total + item["len"] > pick["bucket"]:
                    break
                total, last = total + item["len"], item["ayah"]
            if last is None or last == b:
                raise HTTPException(status_code=400, detail="No whole-ayah trim fits this bucket.")
        seconds = round(sum(i["len"] for i in plan["items"] if i["ayah"] <= last), 2)
        ref = picks_lib.normalize(s, a, last, _quran_data())
        preview = {"ref": ref, "seconds": seconds, "from": pick["ref"], "confirmed": False}
        if not body.confirm:
            return preview
        if any(p["bucket"] == pick["bucket"] and p["mode"] == pick["mode"] and p["ref"] == ref for p in picks):
            raise HTTPException(status_code=409, detail=f"{ref} is already in this list.")
        new = {"id": f"{pick['bucket']}{pick['mode'][0]}-{uuid4().hex[:6]}", "bucket": pick["bucket"],
               "mode": pick["mode"], "ref": ref, "seconds": seconds,
               "over_bucket": seconds > pick["bucket"], "trimmed_from": pick["id"]}
        _save_picks(picks + [new])
    _pick_audit(user, new["id"], "trim", f"{pick['ref']} -> {ref}")
    return {**preview, "confirmed": True, "pick": _pick_view(new, _quran_data(), MAX_VIDEO_SECONDS)}


# --- Downloads: local file first, then the Supabase copy; what is missing is reported ---
def _video_source(filename, object_path):
    """(path, is_temporary) for a video, or None when neither the local nor the Supabase copy exists."""
    path = _safe_history_video_path(filename)
    if path is not None and path.is_file():
        return path, False
    if (
        SUPABASE_SERVICE_KEY and isinstance(object_path, str) and object_path.startswith("videos/")
        and ".." not in object_path.split("/") and isinstance(filename, str)
    ):
        target = CACHE_DIR / "tmp" / "dl" / f"{uuid4().hex}_{Path(filename).name}"
        try:
            if _download_media_file(object_path, target):
                return target, True
        except Exception as exc:
            LOGGER.error("Supabase fallback download failed: %s", _redact_secrets(str(exc)))
    return None


def _video_available(filename, object_path):
    path = _safe_history_video_path(filename)
    return bool(path is not None and path.is_file()) or bool(
        SUPABASE_SERVICE_KEY and isinstance(object_path, str) and object_path.startswith("videos/")
    )


def _missing_note(missing):
    return "These videos were not available and are not in this ZIP:\n" + "\n".join(missing) + "\n"


# ---------------------------------------------------------------------------------------------
# Accounts: profile, deactivate/delete, and admin user management.
# Authoritative status lives in state/users.json (server-only). user_metadata is client-writable,
# so it is only ever used for display_name / avatar presence, and re-validated on read.
# ---------------------------------------------------------------------------------------------
USERS_OBJECT = "state/users.json"
AUDIT_OBJECT = "state/audit.json"
USERS_FILE = OUTPUT_DIR / "users.json"
AUDIT_FILE = OUTPUT_DIR / "audit.json"
STATE_LOCK = Lock()
USER_STATE_TTL = 60
_USERS_CACHE = {"at": -1e9, "data": {}}
_AUTH_LIST_CACHE = {"at": -1e9, "users": []}
_SIZES_CACHE = {"at": -1e9, "sizes": {}}
ATTEMPTS = {}
ATTEMPTS_LOCK = Lock()
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_SIZE = 256
AVATAR_MAX_PIXELS = 25_000_000
AVATAR_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
ADMIN_PAGE_SIZE = 50
USER_ID_PATTERN = re.compile(r"[0-9a-fA-F-]{36}\Z")
AUDIT_KEEP = 1000


def _read_state(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_state(path, object_path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    if SUPABASE_SERVICE_KEY:
        try:
            _upload_media_file(path, object_path, "application/json")
        except Exception as exc:
            LOGGER.error("Could not back up %s to Supabase: %s", object_path, _redact_secrets(str(exc)))


def _pull_account_state():
    if not SUPABASE_SERVICE_KEY:
        return
    for object_path, path in ((USERS_OBJECT, USERS_FILE), (AUDIT_OBJECT, AUDIT_FILE)):
        if path.exists():
            continue
        try:
            _download_media_file(object_path, path)
        except Exception as exc:
            LOGGER.error("Could not restore %s: %s", object_path, _redact_secrets(str(exc)))


def _users_state():
    """{user_id: {blocked, reason, deactivated, signed_out_at}} with a 60 s in-memory cache."""
    now = time.monotonic()
    with STATE_LOCK:
        if now - _USERS_CACHE["at"] > USER_STATE_TTL:
            _USERS_CACHE.update(at=now, data=_read_state(USERS_FILE, {}))
        return _USERS_CACHE["data"]


def _update_user_state(user_id, **fields):
    with STATE_LOCK:
        data = _read_state(USERS_FILE, {})
        entry = {**data.get(user_id, {}), **fields}
        entry = {k: v for k, v in entry.items() if v not in (None, False, "")}
        if entry:
            data[user_id] = entry
        else:
            data.pop(user_id, None)
        _write_state(USERS_FILE, USERS_OBJECT, data)
        _USERS_CACHE.update(at=time.monotonic(), data=data)  # invalidate: next read sees this change
    with AUTH_CACHE_LOCK:
        AUTH_CACHE.clear()
    _AUTH_LIST_CACHE["at"] = -1e9


def _status_of(state):
    return "blocked" if state.get("blocked") else "deactivated" if state.get("deactivated") else "active"


def _job_cancelled(job_id):
    return bool(GENERATION_JOBS.get(job_id, {}).get("cancelled"))


def _cancel_user_jobs(user_id):
    with GENERATION_JOBS_LOCK:
        for job in GENERATION_JOBS.values():
            if job.get("owner_id") == user_id and job["status"] in {"queued", "processing"}:
                if job["status"] == "queued":
                    _refund_job_remaining(job)
                job.update(status="failed", cancelled=True, error="Generation cancelled because the account is unavailable.")
                if job.get("_proc"):
                    _kill_process_tree(job["_proc"])


def _rate_limit(key, limit=5, window=15 * 60):
    now = time.monotonic()
    with ATTEMPTS_LOCK:
        recent = [t for t in ATTEMPTS.get(key, []) if now - t < window]
        if len(recent) >= limit:
            ATTEMPTS[key] = recent
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")
        ATTEMPTS[key] = recent + [now]


# --- Supabase admin API (service role, server-side only) ---
def _admin_auth(method, path, **kwargs):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise HTTPException(status_code=503, detail="Supabase admin access is not configured on the server.")
    try:
        response = requests.request(
            method,
            f"{SUPABASE_URL}/auth/v1/admin{path}",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=20,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Could not reach Supabase.") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found.")
    if not response.ok:
        raise HTTPException(status_code=502, detail="Supabase admin request failed.")
    return response


def _set_user_metadata(user_id, **fields):
    """Merge fields into user_metadata (None removes a key). Read-merge-write so it is safe whether
    GoTrue merges or replaces."""
    current = _admin_auth("GET", f"/users/{user_id}").json().get("user_metadata") or {}
    merged = {k: v for k, v in {**current, **fields}.items() if v is not None}
    nulls = {k: None for k, v in fields.items() if v is None}
    _admin_auth("PUT", f"/users/{user_id}", json={"user_metadata": {**merged, **nulls}})
    with AUTH_CACHE_LOCK:
        AUTH_CACHE.clear()


def _admin_ids():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return set()
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/noor_admins?select=user_id",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=15,
        )
        response.raise_for_status()
        return {row["user_id"] for row in response.json()}
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Could not verify the admin list.") from exc


def _is_last_active_admin(user_id):
    """True if user_id is an admin and no other unblocked admin would remain."""
    admins = _admin_ids()
    if user_id not in admins:
        return False
    state = _users_state()
    return not any(a != user_id and not (state.get(a) or {}).get("blocked") for a in admins)


def _verify_password(user, password):
    response = _supabase_request(
        "POST", "/auth/v1/token?grant_type=password", SUPABASE_KEY,
        json={"email": user["email"], "password": password},
    )
    if response.status_code in (400, 401, 403, 422):
        raise HTTPException(status_code=403, detail="Current password is incorrect.")
    if not response.ok:
        raise HTTPException(status_code=502, detail="Could not verify the password.")


# --- profile helpers ---
def _clean_text(value, limit):
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit]


def _avatar_path(user_id):
    return f"avatars/{user_id}.webp"


def _display_name(user):
    meta = user.get("metadata") or {}
    name = _clean_text(meta.get("display_name"), 40) or _clean_text(meta.get("full_name"), 40)
    return name if len(name) >= 2 else (user.get("email") or "User").split("@")[0][:40]


def _avatar_url(user):
    if (user.get("metadata") or {}).get("avatar_path") != _avatar_path(user["id"]) or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return _signed_media_url(_avatar_path(user["id"]))
    except Exception:
        return None


def _profile(user):
    state = _users_state().get(user["id"]) or {}
    return {
        "id": user["id"],
        "email": user["email"],
        "email_confirmed": user.get("email_confirmed", True),
        "new_email": user.get("new_email", ""),
        "display_name": _display_name(user),
        "avatar_url": _avatar_url(user),
        "status": _status_of(state),
        "is_admin": user["is_admin"],
        "premium_access": user["premium_access"],
        "created_at": user.get("created_at"),
    }


def _storage_sizes():
    """{object_path: bytes} for videos/, ayah-images/ and avatars/, cached 60 s."""
    now = time.monotonic()
    if now - _SIZES_CACHE["at"] > 60:
        sizes = {}
        if SUPABASE_SERVICE_KEY:
            try:
                for prefix in ("videos", "ayah-images", "avatars"):
                    for item in _list_all_media_objects(prefix):
                        sizes[f"{prefix}/{item['name']}"] = int((item.get("metadata") or {}).get("size") or 0)
            except Exception as exc:
                LOGGER.warning("Could not list storage sizes: %s", _redact_secrets(str(exc)))
        _SIZES_CACHE.update(at=now, sizes=sizes)
    return _SIZES_CACHE["sizes"]


def _entry_object_path(entry):
    if entry.get("object_path"):
        return entry["object_path"]
    return f"ayah-images/{entry['filename']}" if entry.get("type") == "image" and entry.get("filename") else None


def _user_entries(user_id):
    with HISTORY_LOCK:
        entries = _read_video_history_unlocked()["users"].get(user_id, [])
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _usage(user_id):
    sizes = _storage_sizes()
    entries = _user_entries(user_id)
    total = sizes.get(_avatar_path(user_id), 0)
    for entry in entries:
        local = _safe_history_video_path(entry.get("filename"))
        if local is not None and local.is_file():
            total += local.stat().st_size
        else:
            total += sizes.get(_entry_object_path(entry), 0)
    images = sum(e.get("type") == "image" for e in entries)
    return {"videos": len(entries) - images, "images": images, "bytes": total}


def _purge_user_content(user_id, avatar=True):
    """Delete the user's stored files (raises on storage failure, nothing else is touched then) and history."""
    entries = _user_entries(user_id)
    paths = {p for p in map(_entry_object_path, entries) if p}
    if avatar:
        paths.add(_avatar_path(user_id))
    paths = sorted(paths)
    for start in range(0, len(paths), 100):
        _delete_media_objects(paths[start:start + 100])
    for entry in entries:
        local = _safe_history_video_path(entry.get("filename"))
        if local is not None:
            local.unlink(missing_ok=True)
    with HISTORY_LOCK:
        history = _read_video_history_unlocked()
        history["users"].pop(user_id, None)
        _write_video_history_unlocked(history)
    _SIZES_CACHE["at"] = -1e9
    return len(entries)


# --- /api/me ---
class ProfileUpdate(BaseModel):
    display_name: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class DeleteAccount(BaseModel):
    password: str
    confirm: str


class BlockRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


@app.get("/api/me")
def get_me(user=Depends(get_user_allow_deactivated)):
    return _profile(user)


@app.get("/api/me/usage")
def get_my_usage(user=Depends(get_current_user)):
    return _usage(user["id"])


@app.put("/api/me")
def update_me(req: ProfileUpdate, user=Depends(get_current_user)):
    name = _clean_text(req.display_name, 41)
    if not 2 <= len(name) <= 40:
        raise HTTPException(status_code=400, detail="Name must be 2 to 40 characters.")
    _set_user_metadata(user["id"], display_name=name)
    return {**_profile(user), "display_name": name}


def _decode_avatar(data: bytes) -> bytes:
    if not data.startswith(AVATAR_MAGIC) and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise HTTPException(status_code=400, detail="Photo must be a PNG, JPG or WebP image.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > AVATAR_MAX_PIXELS:
            raise HTTPException(status_code=400, detail="Photo dimensions are too large.")
        image = ImageOps.exif_transpose(image)
        image = ImageOps.fit(image.convert("RGBA" if "A" in image.getbands() else "RGB"), (AVATAR_SIZE, AVATAR_SIZE))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Photo could not be read.") from exc
    out = io.BytesIO()
    image.save(out, "WEBP", quality=85)
    return out.getvalue()


def _put_media_bytes(object_path, data, content_type):
    _storage_request(
        "POST", f"/object/{quote(MEDIA_BUCKET, safe='')}/{quote(object_path, safe='/')}",
        data=data, headers={"Content-Type": content_type, "x-upsert": "true"},
    )


@app.post("/api/me/avatar")
async def upload_avatar(request: Request, user=Depends(get_current_user)):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Photo must be 2 MB or smaller.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > AVATAR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Photo must be 2 MB or smaller.")
    webp = _decode_avatar(bytes(body))
    _put_media_bytes(_avatar_path(user["id"]), webp, "image/webp")
    _set_user_metadata(user["id"], avatar_path=_avatar_path(user["id"]))
    return {"avatar_url": _signed_media_url(_avatar_path(user["id"]))}


@app.delete("/api/me/avatar")
def remove_avatar(user=Depends(get_current_user)):
    _delete_media_objects([_avatar_path(user["id"])])
    _set_user_metadata(user["id"], avatar_path=None)
    return {"avatar_url": None}


@app.post("/api/me/password")
def change_password(req: PasswordChange, user=Depends(get_current_user)):
    _rate_limit(f"pw:{user['id']}")
    if len(req.new_password) < 8 or len(req.new_password) > 128:
        raise HTTPException(status_code=400, detail="New password must be 8 to 128 characters.")
    _verify_password(user, req.current_password)
    _admin_auth("PUT", f"/users/{user['id']}", json={"password": req.new_password})
    return {"changed": True}


@app.post("/api/me/deactivate")
def deactivate_me(user=Depends(get_current_user)):
    _update_user_state(user["id"], deactivated=True)
    _cancel_user_jobs(user["id"])
    try:
        _set_user_metadata(user["id"], status="deactivated")
    except HTTPException:
        LOGGER.warning("Could not mirror deactivated status to user_metadata")
    return {"status": "deactivated"}


@app.post("/api/me/reactivate")
def reactivate_me(user=Depends(get_user_allow_deactivated)):
    _update_user_state(user["id"], deactivated=False)
    try:
        _set_user_metadata(user["id"], status=None)
    except HTTPException:
        LOGGER.warning("Could not clear deactivated status in user_metadata")
    return {"status": "active"}


@app.post("/api/me/delete")
def delete_me(req: DeleteAccount, user=Depends(get_user_allow_deactivated)):
    if req.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm.")
    _rate_limit(f"pw:{user['id']}")
    _verify_password(user, req.password)
    if _is_last_active_admin(user["id"]):
        raise HTTPException(status_code=409, detail="You are the last admin and cannot delete this account.")
    _cancel_user_jobs(user["id"])
    removed = _purge_user_content(user["id"])
    _admin_auth("DELETE", f"/users/{user['id']}")
    _update_user_state(user["id"], blocked=False, deactivated=False, reason="", signed_out_at=None)
    return {"deleted": True, "items_removed": removed}


@app.get("/api/me/export")
def export_me(user=Depends(get_current_user)):
    profile = _profile(user)
    profile.pop("avatar_url")
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "history": [{k: v for k, v in e.items() if k != "object_path"} for e in _user_entries(user["id"])],
    }


# --- admin: users ---
def _audit(actor, target_id, target_email, action, reason=""):
    with STATE_LOCK:
        log = _read_state(AUDIT_FILE, [])
        log.append({
            "at": datetime.now(timezone.utc).isoformat(), "actor_id": actor["id"], "actor_email": actor["email"],
            "target_id": target_id, "target_email": target_email, "action": action, "reason": reason,
        })
        _write_state(AUDIT_FILE, AUDIT_OBJECT, log[-AUDIT_KEEP:])


def _all_auth_users():
    now = time.monotonic()
    if now - _AUTH_LIST_CACHE["at"] > 20:
        users, page = [], 1
        while page <= 10:
            batch = _admin_auth("GET", f"/users?page={page}&per_page=1000").json().get("users", [])
            users.extend(batch)
            if len(batch) < 1000:
                break
            page += 1
        _AUTH_LIST_CACHE.update(at=now, users=users)
    return _AUTH_LIST_CACHE["users"]


def _premium_ids():
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/noor_user_access?select=user_id&premium_access=eq.true",
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            timeout=15,
        )
        response.raise_for_status()
        return {row["user_id"] for row in response.json()}
    except (requests.RequestException, ValueError, KeyError):
        return set()


def _signed_urls(paths):
    """Batch-sign storage paths -> {path: url}; best effort."""
    if not paths:
        return {}
    try:
        response = _storage_request(
            "POST", f"/object/sign/{quote(MEDIA_BUCKET, safe='')}",
            json={"expiresIn": 3600, "paths": list(paths)},
        )
        return {
            row["path"]: (row["signedURL"] if row["signedURL"].startswith("http") else f"{SUPABASE_URL}/storage/v1{row['signedURL']}")
            for row in response.json() if row.get("signedURL")
        }
    except Exception:
        return {}


@app.get("/api/admin/users")
def admin_users(q: str = "", status: str = "all", page: int = 1, user=Depends(require_admin)):
    if status not in {"all", "active", "blocked", "deactivated"}:
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    state, premium, admins = _users_state(), _premium_ids(), _admin_ids()
    rows = []
    for item in _all_auth_users():
        meta = item.get("user_metadata") or {}
        entry = state.get(item["id"]) or {}
        rows.append({
            "id": item["id"],
            "email": item.get("email") or "",
            "display_name": _clean_text(meta.get("display_name"), 40) or _clean_text(meta.get("full_name"), 40),
            "created_at": item.get("created_at"),
            "last_sign_in_at": item.get("last_sign_in_at"),
            "status": _status_of(entry),
            "reason": entry.get("reason", ""),
            "premium_access": item["id"] in premium or item["id"] in admins,
            "is_admin": item["id"] in admins,
            "_avatar": meta.get("avatar_path") if meta.get("avatar_path") == _avatar_path(item["id"]) else None,
        })
    stats = {"total": len(rows), "premium": sum(r["premium_access"] for r in rows)}
    needle = q.strip().lower()
    rows = [
        r for r in rows
        if (status == "all" or r["status"] == status)
        and (not needle or needle in f"{r['email']} {r['display_name']}".lower())
    ]
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    page = max(1, page)
    window = rows[(page - 1) * ADMIN_PAGE_SIZE: page * ADMIN_PAGE_SIZE]
    urls = _signed_urls([r["_avatar"] for r in window if r["_avatar"]])
    today = _today()
    for r in window:
        r["avatar_url"] = urls.get(r.pop("_avatar"))
        day, count = DAILY_USAGE.get(r["id"], (None, 0))
        r["videos_today"] = count if day == today else 0
        r["storage_bytes"] = _usage(r["id"])["bytes"]
    return {"users": window, "total": len(rows), "page": page, "per_page": ADMIN_PAGE_SIZE, "stats": stats}


def _target(user_id):
    if USER_ID_PATTERN.fullmatch(user_id) is None:
        raise HTTPException(status_code=400, detail="Invalid user id.")
    return _admin_auth("GET", f"/users/{user_id}").json()


@app.post("/api/admin/users/{user_id}/block")
def admin_block(user_id: str, req: BlockRequest, user=Depends(require_admin)):
    target = _target(user_id)
    if user_id == user["id"] or _is_last_active_admin(user_id):
        raise HTTPException(status_code=409, detail="You cannot block yourself or the last admin.")
    reason = _clean_text(req.reason, 200)
    _admin_auth("PUT", f"/users/{user_id}", json={"ban_duration": "876000h"})
    _update_user_state(user_id, blocked=True, reason=reason)
    _cancel_user_jobs(user_id)
    _audit(user, user_id, target.get("email", ""), "block", reason)
    return {"status": "blocked"}


@app.post("/api/admin/users/{user_id}/unblock")
def admin_unblock(user_id: str, user=Depends(require_admin)):
    target = _target(user_id)
    _admin_auth("PUT", f"/users/{user_id}", json={"ban_duration": "none"})
    _update_user_state(user_id, blocked=False, reason="")
    _audit(user, user_id, target.get("email", ""), "unblock")
    return {"status": "active"}


@app.post("/api/admin/users/{user_id}/signout")
def admin_signout(user_id: str, user=Depends(require_admin)):
    target = _target(user_id)
    # Supabase has no revoke-by-id endpoint; instead reject every token whose login predates now.
    _update_user_state(user_id, signed_out_at=int(time.time()))
    _audit(user, user_id, target.get("email", ""), "signout")
    return {"signed_out": True}


@app.post("/api/admin/users/{user_id}/purge")
def admin_purge(user_id: str, user=Depends(require_admin)):
    target = _target(user_id)
    _cancel_user_jobs(user_id)
    removed = _purge_user_content(user_id, avatar=False)
    _audit(user, user_id, target.get("email", ""), "purge", f"{removed} items")
    return {"items_removed": removed}


@app.get("/api/admin/audit")
def admin_audit(offset: int = 0, user=Depends(require_admin)):
    with STATE_LOCK:
        log = _read_state(AUDIT_FILE, [])
    offset = max(0, offset)
    return {"entries": log[::-1][offset:offset + ADMIN_PAGE_SIZE], "total": len(log)}


# 7. Static Web Files Serve Karein (MUST BE AT THE VERY BOTTOM)
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")