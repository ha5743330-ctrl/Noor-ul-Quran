import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import UUID, uuid4

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 1. Environment Variables Load Karein
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
MEDIA_BUCKET = os.getenv("SUPABASE_MEDIA_BUCKET", "noor-media")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "output"))
BACKGROUND_DIR = Path(os.getenv("BACKGROUNDS_DIR", BASE_DIR / "backgrounds"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", BASE_DIR / "cache"))
ADMIN_CONTENT_FILE = BASE_DIR / "data" / "admin_content.json"
ADMIN_AUDIO_DIR = BASE_DIR / "audio" / "admin"
MAX_BACKGROUND_BYTES = 50 * 1024 * 1024
MAX_TEXT_LENGTH = 5000
ALLOWED_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
}

# 2. FastAPI App Initialize Karein
app = FastAPI(title="Noor ul Quran API Engine")
GENERATION_LOCK = Lock()
GENERATION_JOBS_LOCK = Lock()
GENERATION_JOBS = {}
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
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# 5. Request Body Schema
class GenerateRequest(BaseModel):
    mode: str  # 'pick', 'picks', 'random'
    verse: Optional[str] = None  # e.g. "55:1-8"
    random_count: int = 1
    max_duration: int = 60
    quality: str = "balanced"
    content_mode: str = "full"


class PremiumAccessRequest(BaseModel):
    enabled: bool


class VerseContentRequest(BaseModel):
    arabic_text: str = ""
    urdu_text: str = ""


def _load_admin_content():
    if not ADMIN_CONTENT_FILE.exists():
        return {"verses": {}}
    try:
        content = json.loads(ADMIN_CONTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Admin verse content could not be read.") from exc
    if not isinstance(content, dict) or not isinstance(content.get("verses"), dict):
        raise HTTPException(status_code=500, detail="Admin verse content has an invalid format.")
    return content


def _save_admin_content(content):
    ADMIN_CONTENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ADMIN_CONTENT_FILE.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if SUPABASE_SERVICE_KEY:
        _upload_media_file(ADMIN_CONTENT_FILE, "content/ayah-overrides.json", "application/json")


def _get_verse_content(surah: int, ayah: int):
    content = _load_admin_content()
    verse = content["verses"].get(f"{surah}:{ayah}", {})
    audio = verse.get("audio", {})
    result = {
        "surah": surah,
        "ayah": ayah,
        "arabic_text": verse.get("arabic_text", ""),
        "urdu_text": verse.get("urdu_text", ""),
        "arabic_audio": bool(audio.get("arabic")),
        "urdu_audio": bool(audio.get("urdu")),
    }
    return result


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


def _list_media_objects(prefix: str):
    response = _storage_request(
        "POST",
        f"/object/list/{quote(MEDIA_BUCKET, safe='')}",
        json={"prefix": prefix, "limit": 100, "offset": 0, "sortBy": {"column": "name", "order": "asc"}},
    )
    return response.json()


def _media_object_exists(object_path: str) -> bool:
    prefix, filename = object_path.rsplit("/", 1)
    return any(
        item.get("name") in {filename, object_path}
        for item in _list_media_objects(prefix)
    )


def _upload_media_file(local_path: Path, object_path: str, content_type: str):
    if local_path.stat().st_size > MAX_BACKGROUND_BYTES:
        raise HTTPException(status_code=413, detail="Supabase Free Storage accepts files up to 50 MB.")
    with local_path.open("rb") as media_file:
        _storage_request(
            "POST",
            f"/object/{quote(MEDIA_BUCKET, safe='')}/{quote(object_path, safe='/')}",
            data=media_file,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )


def _download_media_file(object_path: str, local_path: Path) -> bool:
    response = _storage_request(
        "GET",
        f"/object/authenticated/{quote(MEDIA_BUCKET, safe='')}/{quote(object_path, safe='/')}",
        stream=True,
    )
    if response.status_code == 404:
        return False
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("wb") as local_file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                local_file.write(chunk)
    return True


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

    for item in _list_media_objects("backgrounds"):
        object_name = item.get("name", "")
        filename = Path(object_name).name
        if Path(filename).suffix.lower() not in {".mp4", ".mov"}:
            continue
        object_path = object_name if object_name.startswith("backgrounds/") else f"backgrounds/{filename}"
        local_path = BACKGROUND_DIR / filename
        if not local_path.exists():
            _download_media_file(object_path, local_path)

    for object_path, local_path in (
        ("metadata/captions.csv", OUTPUT_DIR / "captions.csv"),
        ("state/used.json", CACHE_DIR / "used.json"),
        ("content/ayah-overrides.json", ADMIN_CONTENT_FILE),
    ):
        if _media_object_exists(object_path):
            _download_media_file(object_path, local_path)

    for item in _list_media_objects("content/audio"):
        object_name = item.get("name", "")
        filename = Path(object_name).name
        if not filename:
            continue
        object_path = object_name if object_name.startswith("content/audio/") else f"content/audio/{filename}"
        local_path = ADMIN_AUDIO_DIR / filename
        if not local_path.exists():
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


def _latest_generated_video() -> Optional[str]:
    if not OUTPUT_DIR.exists():
        return None
    videos = sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return videos[0].name if videos else None


# 6. API Endpoints
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "online", "system": "Noor ul Quran Engine"}


@app.get("/api/access")
def get_access(user=Depends(get_current_user)):
    return {
        "user_id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "premium_access": user["premium_access"],
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
        objects = _list_media_objects("backgrounds")
        return [
            {
                "filename": item["name"],
                "size": (item.get("metadata") or {}).get("size", 0),
            }
            for item in objects
            if Path(item.get("name", "")).suffix.lower() in {".mp4", ".mov"}
        ]

    clips = sorted(
        (path for path in BACKGROUND_DIR.iterdir() if path.suffix.lower() in {".mp4", ".mov"}),
        key=lambda path: path.name.lower(),
    )
    return [{"filename": clip.name, "size": clip.stat().st_size} for clip in clips]


@app.post("/api/admin/backgrounds")
async def upload_background(request: Request, user=Depends(require_admin)):
    original_name = Path(request.query_params.get("filename", "")).name
    extension = Path(original_name).suffix.lower()
    if extension not in {".mp4", ".mov"}:
        raise HTTPException(status_code=400, detail="Upload an MP4 or MOV video clip.")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BACKGROUND_BYTES:
        raise HTTPException(status_code=413, detail="Background clip must be 50 MB or smaller.")

    stored_name = f"{uuid4().hex}{extension}"
    destination = BACKGROUND_DIR / stored_name
    total = 0
    try:
        with destination.open("wb") as output_file:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BACKGROUND_BYTES:
                    raise HTTPException(status_code=413, detail="Background clip must be 50 MB or smaller.")
                output_file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded clip is empty.")
    if SUPABASE_SERVICE_KEY:
        _upload_media_file(destination, f"backgrounds/{stored_name}", "video/mp4" if extension == ".mp4" else "video/quicktime")
    return {"filename": stored_name, "size": total}


def _validate_verse(surah: int, ayah: int):
    if not 1 <= surah <= 114:
        raise HTTPException(status_code=400, detail="Surah must be between 1 and 114.")
    quran_file = BASE_DIR / "data" / "quran.json"
    if not quran_file.exists():
        raise HTTPException(status_code=503, detail="Quran data is unavailable on the server.")
    try:
        quran = json.loads(quran_file.read_text(encoding="utf-8"))
        verse_count = int(quran["surahs"][str(surah)]["n"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Quran data could not be read.") from exc
    if not 1 <= ayah <= verse_count:
        raise HTTPException(status_code=400, detail=f"Ayah must be between 1 and {verse_count} for this surah.")


@app.get("/api/admin/verses/{surah}/{ayah}")
def get_admin_verse_content(surah: int, ayah: int, user=Depends(require_admin)):
    _validate_verse(surah, ayah)
    return _get_verse_content(surah, ayah)


@app.put("/api/admin/verses/{surah}/{ayah}")
def update_admin_verse_content(
    surah: int,
    ayah: int,
    req: VerseContentRequest,
    user=Depends(require_admin),
):
    _validate_verse(surah, ayah)
    if len(req.arabic_text) > MAX_TEXT_LENGTH or len(req.urdu_text) > MAX_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Text must be {MAX_TEXT_LENGTH} characters or fewer.")

    content = _load_admin_content()
    key = f"{surah}:{ayah}"
    verse = content["verses"].setdefault(key, {})
    verse["arabic_text"] = req.arabic_text.strip()
    verse["urdu_text"] = req.urdu_text.strip()
    verse.setdefault("audio", {})
    if not verse["arabic_text"] and not verse["urdu_text"] and not any(verse["audio"].values()):
        del content["verses"][key]
    _save_admin_content(content)
    return _get_verse_content(surah, ayah)


@app.post("/api/admin/verses/{surah}/{ayah}/audio/{language}")
async def upload_admin_verse_audio(
    surah: int,
    ayah: int,
    language: str,
    request: Request,
    user=Depends(require_admin),
):
    _validate_verse(surah, ayah)
    if language not in {"arabic", "urdu"}:
        raise HTTPException(status_code=400, detail="Audio language must be arabic or urdu.")
    original_name = request.query_params.get("filename", "")
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=400, detail="Upload MP3, M4A, AAC, or WAV audio.")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BACKGROUND_BYTES:
                raise HTTPException(status_code=413, detail="Audio file must be 50 MB or smaller.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid content length.") from exc

    filename = f"{surah:03d}{ayah:03d}_{language}{extension}"
    ADMIN_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    destination = ADMIN_AUDIO_DIR / filename
    total = 0
    try:
        with destination.open("wb") as output_file:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BACKGROUND_BYTES:
                    raise HTTPException(status_code=413, detail="Audio file must be 50 MB or smaller.")
                output_file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded audio file is empty.")

    if SUPABASE_SERVICE_KEY:
        _upload_media_file(
            destination,
            f"content/audio/{filename}",
            ALLOWED_AUDIO_TYPES[extension],
        )

    content = _load_admin_content()
    verse = content["verses"].setdefault(f"{surah}:{ayah}", {})
    audio = verse.setdefault("audio", {})
    previous_extension = audio.get(language)
    audio[language] = extension.lstrip(".")
    _save_admin_content(content)

    if previous_extension and previous_extension != extension.lstrip("."):
        previous_file = ADMIN_AUDIO_DIR / f"{surah:03d}{ayah:03d}_{language}.{previous_extension}"
        previous_file.unlink(missing_ok=True)

    return _get_verse_content(surah, ayah)


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
    if req.content_mode not in {"full", "urdu_only"}:
        raise HTTPException(status_code=400, detail="Content mode must be full or urdu_only.")
    if req.mode != "pick" and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for custom and random generation.")
    if req.max_duration > 60 and not (user["is_admin"] or user["premium_access"]):
        raise HTTPException(status_code=403, detail="Premium access is required for videos longer than 60 seconds.")
    if req.random_count < 1 or req.random_count > 25:
        raise HTTPException(status_code=400, detail="Random count must be between 1 and 25.")
    if req.max_duration < 20 or req.max_duration > 600:
        raise HTTPException(status_code=400, detail="Maximum duration must be between 20 and 600 seconds.")
    if req.quality not in {"balanced", "high"}:
        raise HTTPException(status_code=400, detail="Quality must be balanced or high.")

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

    batch_id = uuid4().hex[:10]
    work_dir = CACHE_DIR / "tmp" / batch_id
    manifest_path = CACHE_DIR / f"batch-{batch_id}.json"
    cmd.extend([
        "--max-dur", str(req.max_duration),
        "--quality", req.quality,
        "--batch-id", batch_id,
        "--work-dir", str(work_dir),
        "--result-json", str(manifest_path),
    ])

    job_id = uuid4().hex
    with GENERATION_JOBS_LOCK:
        if any(
            job["owner_id"] == user["id"] and job["status"] in {"queued", "processing"}
            for job in GENERATION_JOBS.values()
        ):
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
        GENERATION_JOBS[job_id] = {
            "owner_id": user["id"],
            "status": "queued",
            "mode": req.mode,
            "quality": req.quality,
            "error": None,
            "videos": [],
            "manifest_path": str(manifest_path),
        }
    GENERATION_EXECUTOR.submit(
        _run_generation_job,
        job_id,
        cmd,
        req.mode,
        manifest_path,
        work_dir,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Generation queued. Each reel will appear here as soon as it is ready.",
        "videos": [],
    }


def _read_generation_manifest(manifest_path: Path):
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _run_generation_job(job_id, cmd, mode, manifest_path, work_dir):
    with GENERATION_JOBS_LOCK:
        job = GENERATION_JOBS.get(job_id)
        if job:
            job["status"] = "processing"
    try:
        with GENERATION_LOCK:
            _prepare_ephemeral_media()
            completed = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
            videos = _read_generation_manifest(manifest_path)
            if completed.returncode != 0 and not videos:
                raise RuntimeError(completed.stderr or completed.stdout or "Video rendering failed.")
            if not videos:
                raise RuntimeError("No video output was generated.")

            for video in videos:
                video["url"] = f"/output/{quote(video['filename'], safe='')}"
                if SUPABASE_SERVICE_KEY:
                    video_path = OUTPUT_DIR / video["filename"]
                    stored_video = f"videos/{uuid4().hex}_{video['filename']}"
                    _upload_media_file(video_path, stored_video, "video/mp4")
                    video["url"] = _signed_media_url(stored_video)

            if SUPABASE_SERVICE_KEY:
                captions_path = OUTPUT_DIR / "captions.csv"
                used_path = CACHE_DIR / "used.json"
                if captions_path.exists():
                    _upload_media_file(captions_path, "metadata/captions.csv", "text/csv")
                if used_path.exists():
                    _upload_media_file(used_path, "state/used.json", "application/json")

        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                job["videos"] = videos
                job["status"] = "completed" if completed.returncode == 0 else "partial"
                job["message"] = "All reels are ready." if completed.returncode == 0 else "Some reels could not be rendered; completed reels are ready."
    except Exception as exc:
        with GENERATION_JOBS_LOCK:
            job = GENERATION_JOBS.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
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

    if response["status"] in {"queued", "processing"}:
        response["videos"] = _read_generation_manifest(manifest_path)

    for video in response["videos"]:
        video.setdefault("url", f"/output/{quote(video['filename'], safe='')}")
    response["job_id"] = job_id
    return response


# 7. Static Web Files Serve Karein (MUST BE AT THE VERY BOTTOM)
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")