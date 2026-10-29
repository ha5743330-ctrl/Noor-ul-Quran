import base64
import functools
import json
import hashlib
import hmac
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
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import features
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

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
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "30"))
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
class GenerateRequest(BaseModel):
    mode: str  # 'pick', 'picks', 'random'
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
        and path.startswith(("videos/", "ayah-images/"))
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


def get_current_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    token = authorization[7:].strip()
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with AUTH_CACHE_LOCK:
        cached = AUTH_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return dict(cached[1])
    user = _fetch_current_user(token)
    with AUTH_CACHE_LOCK:
        if len(AUTH_CACHE) >= 1000:
            AUTH_CACHE.clear()
        AUTH_CACHE[cache_key] = (now + AUTH_CACHE_SECONDS, user)
    return dict(user)


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


def _featured_ayah_references():
    featured_path = BASE_DIR / "data" / "featured_ayahs.json"
    try:
        featured = json.loads(featured_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.exception("Could not load curated ayah data.")
        raise HTTPException(status_code=500, detail="Curated ayah data is unavailable.") from exc
    references = featured.get("ayahs") if isinstance(featured, dict) else None
    if not isinstance(references, list) or not references or any(
        not isinstance(reference, str)
        or re.fullmatch(r"\d{1,3}:\d{1,3}", reference) is None
        for reference in references
    ):
        raise HTTPException(status_code=500, detail="Curated ayah data is invalid.")
    return references


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
    return DAILY_LIMIT - (count if day == _today() else 0)


def _reserve_daily_slot(user):
    if user["is_admin"]:
        return
    with DAILY_USAGE_LOCK:
        day, count = DAILY_USAGE.get(user["id"], (None, 0))
        count = count if day == _today() else 0
        if count >= DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit of {DAILY_LIMIT} videos reached. It resets tomorrow.",
            )
        DAILY_USAGE[user["id"]] = (_today(), count + 1)


def _refund_daily_slot(user_id):
    with DAILY_USAGE_LOCK:
        day, count = DAILY_USAGE.get(user_id, (None, 0))
        if day == _today() and count > 0:
            DAILY_USAGE[user_id] = (day, count - 1)


def _average_render_seconds():
    return sum(RENDER_DURATIONS) / len(RENDER_DURATIONS) if RENDER_DURATIONS else DEFAULT_RENDER_SECONDS


def _queue_info(job_id):
    """Jobs ahead of job_id (running + earlier queued) and a rough wait; call with GENERATION_JOBS_LOCK held."""
    ahead = 0
    for other_id, other in GENERATION_JOBS.items():
        if other_id == job_id:
            break
        if other["status"] in {"queued", "processing"}:
            ahead += 1
    return ahead, round((ahead + 1) * _average_render_seconds())


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
    _owned_history_entries(user["id"], ids)
    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    query = urlencode({
        "u": user["id"],
        "ids": ",".join(ids),
        "exp": expires,
        "sig": _history_zip_signature(user["id"], ids, expires),
    })
    return {"url": f"/api/history/zip?{query}", "item_count": len(ids), "expires_at": expires}


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


def _stream_zip(paths_by_name):
    sink = _ZipStreamSink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for arcname, path in paths_by_name.items():
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = zipfile.ZIP_STORED
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    yield sink.drain()
            yield sink.drain()
    yield sink.drain()


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
    paths_by_name = {}
    for entry in _owned_history_entries(u, id_list):
        path = _safe_history_video_path(entry.get("filename"))
        if path is not None and path.is_file():
            paths_by_name[path.name] = path
    if not paths_by_name:
        raise HTTPException(status_code=410, detail="The selected files have expired.")

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
        _stream_zip(paths_by_name),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="noor-ul-quran-{len(paths_by_name)}-items.zip"',
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/api/access")
def get_access(user=Depends(get_current_user)):
    return {
        "user_id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "premium_access": user["premium_access"],
        "daily_limit": None if user["is_admin"] else DAILY_LIMIT,
        "daily_remaining": _daily_remaining(user),
        "max_video_seconds": None if user["is_admin"] else MAX_VIDEO_SECONDS,
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
    if not 30 <= selected_length <= 180:
        raise HTTPException(status_code=400, detail="Length must be between 30 and 180 seconds.")
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


@app.get("/api/admin/users")
def list_users(user=Depends(require_admin)):
    response = _supabase_request(
        "POST",
        "/rest/v1/rpc/noor_admin_list_users",
        user["token"],
        json={},
    )
    _raise_supabase_error(response)
    return response.json()


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


@app.post("/api/generate", status_code=202)
def generate_reel(req: GenerateRequest, user=Depends(get_current_user)):
    if req.mode not in {"pick", "picks", "random"}:
        raise HTTPException(status_code=400, detail="Invalid generation mode.")
    if req.content_mode not in {"full", "urdu_only", "arabic_only"}:
        raise HTTPException(
            status_code=400,
            detail="Content mode must be full, urdu_only, or arabic_only.",
        )
    if req.content_mode == "arabic_only" and req.music:
        raise HTTPException(
            status_code=400,
            detail="Background music is not available in Arabic-only mode.",
        )
    reciter_id = _validate_reciter_id(
        req.reciter_id, "ar", DEFAULT_ARABIC_RECITER_ID
    )
    if req.content_mode == "arabic_only":
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
    if req.mode != "pick" and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for custom and random generation.")
    if req.max_duration > 60 and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for videos longer than 60 seconds.")
    if req.random_count < 1 or req.random_count > 25:
        raise HTTPException(status_code=400, detail="Random count must be between 1 and 25.")
    if req.max_duration < 30 or req.max_duration > 180:
        raise HTTPException(status_code=400, detail="Maximum duration must be between 30 and 180 seconds.")
    if req.quality not in RENDER_QUALITIES:
        raise HTTPException(status_code=400, detail="Quality must be fast, balanced, or high.")
    if not user["is_admin"] and req.max_duration > MAX_VIDEO_SECONDS:
        raise HTTPException(status_code=400, detail=f"Videos are limited to {MAX_VIDEO_SECONDS} seconds on the free server.")
    validate_video_styles(req.styles)
    _require_raqm_for_generation(req.content_mode)

    cmd = [sys.executable, str(BASE_DIR / "make.py")]
    if req.mode == "pick" and req.verse:
        cmd.extend(["--pick", req.verse])
    elif req.mode == "picks":
        cmd.extend(["--picks", str(BASE_DIR / "picks.txt")])
    elif req.mode == "random":
        cmd.extend(["--random", str(req.random_count)])
    else:
        raise HTTPException(status_code=400, detail="Verse is required for single-pick generation.")
    if req.content_mode == "urdu_only":
        cmd.append("--urdu-only")
    elif req.content_mode == "arabic_only":
        cmd.append("--arabic-only")
    cmd.extend(["--reciter", reciter_id])
    if req.content_mode != "arabic_only":
        cmd.extend(["--urdu-voice", urdu_voice_id])

    # Background music argument agar select kiya gaya ho (yeh ab bilkul theek jagah par hai):
    if music_filename is not None:
        cmd.extend(["--music", music_filename])

    batch_id = uuid4().hex[:10]
    work_dir = CACHE_DIR / "tmp" / batch_id
    manifest_path = CACHE_DIR / f"batch-{batch_id}.json"
    if req.styles:
        style_path = work_dir / "style.json"
        cmd.extend(["--style-json", str(style_path)])
    cmd.extend([
        "--max-dur", str(req.max_duration),
        "--quality", req.quality,
        "--batch-id", batch_id,
        "--work-dir", str(work_dir),
        "--result-json", str(manifest_path),
    ])

    reel_count = req.random_count if req.mode == "random" else 1
    if req.mode == "picks":
        reel_count = max(1, sum(
            1 for line in (BASE_DIR / "picks.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ))

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
        _reserve_daily_slot(user)
        GENERATION_JOBS[job_id] = {
            "owner_id": user["id"],
            "status": "queued",
            "mode": req.mode,
            "quality": req.quality,
            "error": None,
            "videos": [],
            "warnings": [],
            "manifest_path": str(manifest_path),
        }
        queue_ahead, wait_seconds = _queue_info(job_id)
    GENERATION_EXECUTOR.submit(
        _run_generation_job,
        job_id,
        cmd,
        req.mode,
        manifest_path,
        work_dir,
        req.styles,
        user["id"],
        req.content_mode,
        req.quality,
        RENDER_TIMEOUT_SECONDS * reel_count,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Generation queued. Each reel will appear here as soon as it is ready.",
        "videos": [],
        "queue_ahead": queue_ahead,
        "estimated_wait_seconds": wait_seconds,
        "daily_remaining": _daily_remaining(user),
    }


def _read_generation_manifest(manifest_path: Path):
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest if isinstance(manifest, list) else []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []


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


def _run_render(cmd, timeout):
    """Run make.py; on timeout kill it and every FFmpeg child it started."""
    group_args = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt" else {"start_new_session": True}
    )
    process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **group_args,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        process.communicate()
        raise RuntimeError(f"Rendering took longer than {timeout} seconds and was stopped.")
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
        if job:
            job["status"] = "processing"
    started = time.monotonic()
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
            completed = _run_render(cmd, timeout)
            videos = _read_generation_manifest(manifest_path)
            if completed.returncode != 0 and not videos:
                raise RuntimeError(completed.stderr or completed.stdout or "Video rendering failed.")
            if not videos:
                raise RuntimeError("No video output was generated.")

            for video in videos:
                if SUPABASE_SERVICE_KEY:
                    video_path = OUTPUT_DIR / video["filename"]
                    stored_video = f"videos/{uuid4().hex}_{video['filename']}"
                    _upload_media_file(video_path, stored_video, "video/mp4")
                    video["object_path"] = stored_video
                    video["url"] = _signed_media_url(stored_video)

            if SUPABASE_SERVICE_KEY:
                captions_path = OUTPUT_DIR / "captions.csv"
                used_path = CACHE_DIR / "used.json"
                if captions_path.exists():
                    _upload_media_file(captions_path, "metadata/captions.csv", "text/csv")
                if used_path.exists():
                    _upload_media_file(used_path, "state/used.json", "application/json")

        status = "completed" if completed.returncode == 0 else "partial"
        try:
            if user_id is not None:
                _record_video_history(user_id, videos, content_mode, quality, styles)
        except Exception as history_error:
            LOGGER.error(
                "Could not update generated-video history (job_id=%s, user_id=%s): %s",
                job_id,
                user_id,
                _redact_secrets(str(history_error)),
            )

        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                job["videos"] = videos
                job["warnings"] = list(dict.fromkeys(
                    warning
                    for video in videos
                    for warning in video.get("warnings", [])
                ))
                job["status"] = status
                job["message"] = "All reels are ready." if completed.returncode == 0 else "Some reels could not be rendered; completed reels are ready."
        RENDER_DURATIONS.append((time.monotonic() - started) / max(1, len(videos)))
        del RENDER_DURATIONS[:-10]
    except Exception as exc:
        safe_error = _redact_secrets(str(exc))
        LOGGER.error(
            "Generation job failed (job_id=%s, user_id=%s): %s",
            job_id,
            user_id,
            safe_error,
        )
        if user_id is not None:
            _refund_daily_slot(user_id)
        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = safe_error
                job["videos"] = _read_generation_manifest(manifest_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        manifest_path.with_suffix(manifest_path.suffix + ".tmp").unlink(missing_ok=True)


@app.get("/api/generate/{job_id}")
def generation_status(job_id: str, user=Depends(get_current_user)):
    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if not job or job["owner_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Generation job not found.")
        response = {key: value for key, value in job.items() if key not in {"owner_id", "manifest_path"}}
        manifest_path = Path(job["manifest_path"])
        if job["status"] in {"queued", "processing"}:
            response["queue_ahead"], response["estimated_wait_seconds"] = _queue_info(job_id)

    if response["status"] in {"queued", "processing"}:
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

    filenames = []
    for video in videos:
        filename = video.get("filename") if isinstance(video, dict) else None
        if not isinstance(filename, str):
            raise HTTPException(status_code=500, detail="The batch contains an invalid video entry.")
        _local_video_path(filename)
        filenames.append(filename)

    expires = int(time.time()) + VIDEO_URL_TTL_SECONDS
    archive_name = f"batch-{job_id}.zip"
    signature = _video_signature(archive_name, expires)
    query = urlencode({"exp": expires, "sig": signature})
    return {
        "url": f"/api/batch-download/{quote(job_id, safe='')}?{query}",
        "filename": f"noor-ul-quran-reels-{job_id[:10]}.zip",
        "video_count": len(filenames),
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

    filenames = []
    video_paths = []
    for video in videos:
        filename = video.get("filename") if isinstance(video, dict) else None
        if not isinstance(filename, str):
            raise HTTPException(status_code=500, detail="The batch contains an invalid video entry.")
        video_paths.append(_local_video_path(filename))
        filenames.append(filename)
    if not video_paths:
        raise HTTPException(status_code=404, detail="This batch has no completed reels to download.")

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


# 7. Static Web Files Serve Karein (MUST BE AT THE VERY BOTTOM)
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")