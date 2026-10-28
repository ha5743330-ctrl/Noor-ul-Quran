#!/usr/bin/env python3
"""Noor ul Quran auto reel maker (Arabic tilawat ke waqt Arabic, Urdu audio ke waqt Urdu text).
   python make.py --random 1                      (Standard ~1 min reel)
   python make.py --picks picks.txt --max-dur 90  (Waqia/Long Reel up to 1m 30s)
"""
import argparse, csv, json, os, random, re, shutil, subprocess, sys, time, unicodedata
import ctypes
import functools
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock, get_ident

_DLL_DIRECTORY_HANDLES = []
if os.name == "nt":
    dll_directory = Path(sys.executable).parent
    _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_directory)))
    for dll_name in ("fribidi-0.dll", "libfribidi-0.dll"):
        dll_path = dll_directory / dll_name
        if dll_path.is_file():
            try:
                _DLL_DIRECTORY_HANDLES.append(ctypes.WinDLL(str(dll_path)))
                break
            except OSError:
                continue

from PIL import Image, ImageDraw, ImageFont, ImageOps, features
import requests

RAQM_AVAILABLE = features.check("raqm")

# ---------------- DEFAULT SETTINGS ----------------
W, H, FPS = 1080, 1920, 30
MIN_DUR, MAX_DUR = 20, 60          # Default reel length (20s to 60s / 1 min)
CHANNEL = "Noor ul Quran"
SECONDS_PER_CHUNK = 3.0
LINES_PER_CHUNK = 3
FONT_SIZES = [72, 64, 56, 48, 42]
QURAN_ARABIC_FONT = "quran_arabic"
QURAN_ARABIC_LINE_SPACING = 2.1
BRIGHTNESS = -0.18
INCLUDE_ARABIC_AUDIO = True
CONTENT_MODE = "full"
GAP_AFTER_ARABIC = 0.35
DURATION_TOLERANCE = 0.15
NORMALIZE_AUDIO = True
FFMPEG_CODEC_THREADS = ("-threads", "1")
FFMPEG_FILTER_THREADS = ("-filter_threads", "1", "-filter_complex_threads", "1")
BASE_URL = "https://everyayah.com/data/"
RECITERS_PATH = Path(__file__).parent / "data" / "reciters.json"
RECITER_CONFIG = json.loads(RECITERS_PATH.read_text(encoding="utf-8"))
RECITERS_BY_ID = {reciter["id"]: reciter for reciter in RECITER_CONFIG["reciters"]}
DEFAULT_ARABIC_RECITER_ID = RECITER_CONFIG["defaults"]["ar"]
DEFAULT_URDU_VOICE_ID = RECITER_CONFIG["defaults"]["ur"]
CURRENT_RECITER_ID = DEFAULT_ARABIC_RECITER_ID
CURRENT_URDU_VOICE_ID = DEFAULT_URDU_VOICE_ID
# --------------------------------------------------
ROOT = Path(__file__).parent
QPC_QURAN_PATH = ROOT / "data" / "quran_qpc_hafs.json"
QPC_SHA256_PATH = ROOT / "data" / "quran_qpc_hafs.sha256"


def _load_verified_qpc_quran():
    data = QPC_QURAN_PATH.read_bytes()
    expected = QPC_SHA256_PATH.read_text(encoding="ascii").split()[0]
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Quran text file failed SHA-256 verification; run fetch_assets.py.")
    return json.loads(data.decode("utf-8"))


QPC_QURAN = _load_verified_qpc_quran()
QPC_BISMILLAH_TEXT = QPC_QURAN["surahs"]["1"]["1"]
FONTS = {
    QURAN_ARABIC_FONT: ROOT / "fonts" / "KFGQPC-Uthmanic-Hafs-V22.ttf",
    "amiri_regular": ROOT / "fonts" / "Amiri-Regular.ttf",
    "amiri_bold": ROOT / "fonts" / "Amiri-Bold.ttf",
    "naskh_regular": ROOT / "fonts" / "NotoNaskhArabic-Regular.ttf",
    "naskh_bold": ROOT / "fonts" / "NotoNaskhArabic-Bold.ttf",
    "nastaliq_regular": ROOT / "fonts" / "NotoNastaliqUrdu-Regular.ttf",
    "nastaliq_bold": ROOT / "fonts" / "NotoNastaliqUrdu-Bold.ttf",
}
STYLE_ELEMENTS = {"bismillah", "surah", "label", "arabic", "urdu", "english", "channel"}
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT / "output"))
BACKGROUND_DIR = Path(os.getenv("BACKGROUNDS_DIR", ROOT / "backgrounds"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", ROOT / "cache"))
# Measured durations are runtime state: written under cache/, seeded from the shipped data file.
DURATION_CACHE_FILE = CACHE_DIR / "ayah_durations.json"
CLIP_CACHE_DIR = CACHE_DIR / "backgrounds"
_hardware_encoder = None
_duration_cache = None
_duration_cache_lock = Lock()

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)

def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

def _duration_key(kind, reciter_id, surah, ayah):
    return f"{kind}:{reciter_id}:{surah}:{ayah}"

def _read_duration_file(path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}

def _read_duration_cache():
    raw = _read_duration_file(DURATION_CACHE_FILE)
    cache = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if ":" in key and isinstance(value, (int, float)) and value > 0:
            cache[key] = float(value)
            continue
        if re.fullmatch(r"\d+:\d+", key) and isinstance(value, dict):
            surah, ayah = key.split(":")
            for kind, reciter_id in (
                ("arabic", DEFAULT_ARABIC_RECITER_ID),
                ("urdu", DEFAULT_URDU_VOICE_ID),
            ):
                seconds = value.get(kind)
                if isinstance(seconds, (int, float)) and seconds > 0:
                    cache[_duration_key(kind, reciter_id, surah, ayah)] = float(seconds)
    return cache

def load_duration_cache():
    global _duration_cache
    with _duration_cache_lock:
        if _duration_cache is None:
            _duration_cache = _read_duration_cache()
        return _duration_cache

def save_ayah_duration(surah, ayah, kind, seconds, reciter_id=None):
    global _duration_cache
    if kind not in {"arabic", "urdu"} or seconds <= 0:
        raise ValueError("Invalid ayah audio duration.")
    reciter_id = resolve_reciter_id(kind, reciter_id)
    with _duration_cache_lock:
        cache = _read_duration_cache()
        if _duration_cache:
            cache.update(_duration_cache)
        cache[_duration_key(kind, reciter_id, surah, ayah)] = round(float(seconds), 6)
        DURATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = DURATION_CACHE_FILE.with_name(
            f"{DURATION_CACHE_FILE.stem}.{os.getpid()}.{get_ident()}.tmp"
        )
        temp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(DURATION_CACHE_FILE)
        _duration_cache = cache

def verified_arabic_text(surah, ayah):
    """Arabic ayah text, only ever from the hash-verified KFGQPC Hafs file."""
    return QPC_QURAN["surahs"][str(surah)][str(ayah)]

def resolve_reciter_id(kind, reciter_id=None):
    if kind not in {"arabic", "urdu"}:
        raise ValueError("Audio kind must be arabic or urdu.")
    selected = reciter_id or (
        CURRENT_RECITER_ID if kind == "arabic" else CURRENT_URDU_VOICE_ID
    )
    reciter = RECITERS_BY_ID.get(selected)
    expected_language = "ar" if kind == "arabic" else "ur"
    if reciter is None or reciter["language"] != expected_language:
        raise ValueError(f"Unknown {expected_language} audio reciter: {selected}")
    return selected

def audio_cache_path(kind, surah, ayah, reciter_id=None):
    reciter_id = resolve_reciter_id(kind, reciter_id)
    language = "ar" if kind == "arabic" else "ur"
    return ROOT / "audio" / language / reciter_id / f"{surah:03d}{ayah:03d}.mp3"

def cached_ayah_duration(surah, ayah, kind, reciter_id=None):
    reciter_id = resolve_reciter_id(kind, reciter_id)
    return load_duration_cache().get(
        _duration_key(kind, reciter_id, surah, ayah)
    )

def measure_ayah_audio(surah, ayah, kind, reciter_id=None):
    audio_path = fetch_ayah(kind, surah, ayah, reciter_id)
    if audio_path is None:
        return None
    return cached_ayah_duration(surah, ayah, kind, reciter_id)

def detect_hardware_encoder():
    global _hardware_encoder
    if _hardware_encoder is not None:
        return _hardware_encoder

    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        try:
            probe = subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
                    "-frames:v", "1", "-c:v", encoder, "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            continue
        if probe.returncode == 0:
            _hardware_encoder = encoder
            return encoder

    _hardware_encoder = ""
    return _hardware_encoder

X264_PRESETS = {"fast": "veryfast", "balanced": "fast", "high": "medium"}
QUALITY = "high"

def encode_video(cmd, outfile):
    low_memory = os.getenv("RENDER_MEMORY_PROFILE", "").lower() == "low"
    if not low_memory:
        run(
            cmd
            + [
                "-c:v", "libx264",
                "-preset", X264_PRESETS[QUALITY],
                "-crf", "18",
                "-tune", "film",
                *list(FFMPEG_CODEC_THREADS),
                "-pix_fmt", "yuv420p",
                "-color_range", "tv",
                "-c:a", "aac",
                "-b:a", "192k",
                str(outfile),
            ]
        )
        return

    encoder = detect_hardware_encoder()
    audio_args = ["-c:a", "aac", "-b:a", "192k", str(outfile)]

    if encoder == "h264_nvenc":
        hardware_args = ["-c:v", encoder, "-preset", "p4", "-rc", "vbr", "-b:v", "650k", "-maxrate", "900k", "-bufsize", "1800k"]
    elif encoder == "h264_qsv":
        hardware_args = ["-c:v", encoder, "-preset", "veryfast", "-b:v", "650k", "-maxrate", "900k", "-bufsize", "1800k", "-look_ahead", "0"]
    elif encoder == "h264_amf":
        hardware_args = ["-c:v", encoder, "-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "22"]
    else:
        hardware_args = []

    if hardware_args:
        result = subprocess.run(
            cmd + hardware_args + list(FFMPEG_CODEC_THREADS) + ["-pix_fmt", "yuv420p"] + audio_args,
            check=False,
        )
        if result.returncode == 0:
            return
        print(f"Hardware encoder {encoder} failed; retrying with CPU encoding.", file=sys.stderr)
        Path(outfile).unlink(missing_ok=True)

    run(
        cmd
        + ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24"]
        + list(FFMPEG_CODEC_THREADS)
        + ["-pix_fmt", "yuv420p"]
        + audio_args
    )

def set_video_quality(quality):
    global W, H, FPS, QUALITY
    QUALITY = quality
    if os.getenv("RENDER_MEMORY_PROFILE", "").lower() == "low":
        if quality == "high":
            W, H, FPS = 540, 960, 20
        else:
            W, H, FPS = 480, 854, 20
        return
    if quality == "high":
        W, H, FPS = 1080, 1920, 30
    else:
        W, H, FPS = 720, 1280, 24

def background_playlist(bgs, total, rng):
    unique, sizes = [], {}
    for path in bgs:
        sizes.setdefault(path.stat().st_size, []).append(path)

    for same_size_paths in sizes.values():
        if len(same_size_paths) == 1:
            unique.extend(same_size_paths)
            continue
        digests = set()
        for path in same_size_paths:
            digest_builder = hashlib.sha256()
            with path.open("rb") as clip_file:
                for chunk in iter(lambda: clip_file.read(1024 * 1024), b""):
                    digest_builder.update(chunk)
            digest = digest_builder.digest()
            if digest not in digests:
                unique.append(path)
                digests.add(digest)

    if total <= 0:
        return []
    if not unique:
        raise RuntimeError("No unique background videos are available.")
    if len(unique) == 1:
        print("Only one background clip is available; looping it for the full reel.", file=sys.stderr)
        path = unique[0]
        if dur(path) <= 0:
            raise RuntimeError(f"Background video has invalid duration: {path}")
        return [(path, 0.0, total)]
    if total < 8.0:
        path = rng.choice(unique)
        if dur(path) <= 0:
            raise RuntimeError(f"Background video has invalid duration: {path}")
        return [(path, 0.0, total)]

    xfade_duration = 0.5
    min_contribution = 8.0 - xfade_duration
    max_contribution = 15.0 - xfade_duration
    segment_counts = [
        count for count in range(1, int(total / min_contribution) + 2)
        if 8.0 + (count - 1) * min_contribution <= total
        <= 15.0 + (count - 1) * max_contribution
    ]
    if not segment_counts:
        segment_counts = [1] if total <= 15.0 else [2]
    segment_count = rng.choice(segment_counts)
    contributions = []
    remaining = total
    for index in range(segment_count):
        min_duration = 8.0 if index == 0 else min_contribution
        max_duration = 15.0 if index == 0 else max_contribution
        remaining_count = segment_count - index - 1
        lower = max(min_duration, remaining - remaining_count * max_contribution)
        upper = min(max_duration, remaining - remaining_count * min_contribution)
        contribution = rng.uniform(lower, upper) if upper > lower else lower
        contributions.append(contribution)
        remaining -= contribution
    contributions[-1] += remaining

    playlist, bag, previous = [], [], None
    durations = {path: dur(path) for path in unique}
    while len(playlist) < segment_count:
        if not bag:
            bag = list(unique)
            rng.shuffle(bag)
            if bag[0] == previous:
                swap_index = next(index for index, candidate in enumerate(bag) if candidate != previous)
                bag[0], bag[swap_index] = bag[swap_index], bag[0]
        path = bag.pop(0)
        if durations[path] <= 0:
            raise RuntimeError(f"Background video has invalid duration: {path}")
        index = len(playlist)
        segment_duration = contributions[index] + (xfade_duration if index else 0.0)
        clip_duration = durations[path]
        seek = rng.uniform(0, clip_duration - segment_duration) if clip_duration > segment_duration else 0
        playlist.append((path, seek, segment_duration))
        previous = path
    return playlist

def urdu_digits(n): return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
def strip_harakat_surah(s): return re.sub("[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]", "", s)

def is_generated_reel(path):
    return re.fullmatch(
        r"(?:[A-Za-z0-9_-]+_)?\d{3}_s\d+_\d+-\d+(?:_\d+)?",
        path.stem,
        re.IGNORECASE,
    ) is not None

def still_background():
    """Loop a backgrounds/images/ still into a short clip so renders work with no video clips."""
    images = sorted(p for p in (BACKGROUND_DIR / "images").glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    if not images: return None
    out = CACHE_DIR / "still-background.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_suffix(".tmp.mp4")
        run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-loop", "1", "-framerate", "5", "-t", "10", "-i", str(images[0]),
             "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", str(tmp_out)])
        tmp_out.replace(out)
    return out

def remove_leading_bismillah(text):
    prefix = re.sub(r"\s*[٠-٩]+$", "", QPC_BISMILLAH_TEXT).split()
    words = text.lstrip("\ufeff").split()
    normalize = lambda word: strip_harakat_surah(word).replace("ٱ", "ا")
    expected = [normalize(word) for word in prefix]
    actual = [normalize(word) for word in words[:len(expected)]]
    return " ".join(words[len(expected):]) if actual == expected else text

def ayah_text_without_bismillah(text, surah_number):
    if surah_number not in {1, 9}:
        return remove_leading_bismillah(text)
    return text.lstrip("\ufeff")

def load_video_styles(path):
    if path is None:
        return {}
    try:
        styles = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read video styles from {path}: {exc}") from exc
    if not isinstance(styles, dict):
        raise SystemExit("Video styles must be a JSON object.")
    for element, style in styles.items():
        if element not in STYLE_ELEMENTS or not isinstance(style, dict):
            raise SystemExit(f"Invalid video style element: {element}")
        if any(field not in {"color", "font"} for field in style):
            raise SystemExit(f"Invalid style field for {element}.")
        color = style.get("color")
        if color is not None and (
            not isinstance(color, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
        ):
            raise SystemExit(f"Invalid style color for {element}; use #RRGGBB.")
        font = style.get("font")
        if font is not None and (not isinstance(font, str) or font not in FONTS):
            raise SystemExit(f"Invalid style font for {element}: {font}")
    return styles

def styled_font(styles, element, default):
    return styles.get(element, {}).get("font", default)

def styled_color(styles, element, default):
    color = styles.get(element, {}).get("color")
    if color is None:
        return default
    return tuple(int(color[offset:offset + 2], 16) for offset in (1, 3, 5)) + (default[3],)

def resolve_font_name(name, fallback):
    if name in FONTS and FONTS[name].is_file():
        return name
    if fallback in FONTS and FONTS[fallback].is_file():
        return fallback
    raise SystemExit(f"Required default font file missing: {FONTS.get(fallback, fallback)}")

def require_raqm():
    if not RAQM_AVAILABLE:
        raise SystemExit(
            "Pillow RAQM text shaping is unavailable. A libfribidi-0.dll beside "
            "Python is not sufficient; use a Pillow build with libraqm enabled."
        )

def fetch_ayah(kind, s, a, reciter_id=None):
    reciter_id = resolve_reciter_id(kind, reciter_id)
    audio_path = audio_cache_path(kind, s, a, reciter_id)
    if not audio_path.exists() or audio_path.stat().st_size <= 1000:
        folder = RECITERS_BY_ID[reciter_id]["folder"]
        url = f"{BASE_URL}{folder}/{s:03d}{a:03d}.mp3"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                response = requests.get(url, timeout=60)
                if response.status_code == 200 and len(response.content) > 1000:
                    audio_path.write_bytes(response.content)
                    break
                if response.status_code == 404:
                    return None
            except requests.RequestException:
                time.sleep(2)
        else:
            return None

    if cached_ayah_duration(s, a, kind, reciter_id) is None:
        save_ayah_duration(s, a, kind, dur(audio_path), reciter_id)
    return audio_path

_clip_cache = {}
def ayah_clip(s, a, content_mode=None, reciter_id=None, urdu_voice_id=None):
    content_mode = content_mode or (
        CONTENT_MODE if CONTENT_MODE == "arabic_only"
        else "full" if INCLUDE_ARABIC_AUDIO else "urdu_only"
    )
    reciter_id = resolve_reciter_id("arabic", reciter_id)
    urdu_voice_id = resolve_reciter_id("urdu", urdu_voice_id)
    cache_key = (s, a, content_mode, reciter_id, urdu_voice_id)
    if cache_key in _clip_cache: return _clip_cache[cache_key]
    if content_mode == "arabic_only":
        ar = fetch_ayah("arabic", s, a, reciter_id)
        if ar is None:
            c = None
        else:
            da = cached_ayah_duration(s, a, "arabic", reciter_id)
            c = {
                "segs": [(str(ar), da, 0.0)],
                "ar_dur": da,
                "u0": 0.0,
                "ur_dur": 0.0,
                "len": da,
            }
        _clip_cache[cache_key] = c
        return c
    include_arabic = content_mode == "full"
    ur = fetch_ayah("urdu", s, a, urdu_voice_id)
    ar = fetch_ayah("arabic", s, a, reciter_id) if include_arabic else None
    if ur is None or (include_arabic and ar is None):
        c = None
    else:
        segs, u0 = [], 0.0
        da = cached_ayah_duration(s, a, "arabic", reciter_id) if ar else 0.0
        du = cached_ayah_duration(s, a, "urdu", urdu_voice_id)
        if ar:
            segs.append((str(ar), da, GAP_AFTER_ARABIC))
            u0 = da + GAP_AFTER_ARABIC
        segs.append((str(ur), du, 0.0))
        c = {"segs": segs, "ar_dur": da, "u0": u0, "ur_dur": du, "len": u0 + du}
    _clip_cache[cache_key] = c
    return c

def load_font(name, size, fallback="amiri_bold"):
    name = resolve_font_name(name, fallback)
    path = FONTS[name]
    require_raqm()
    return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)

def wrap(text, font, maxw, language="ur"):
    words = text.split()
    if language == "ar":
        attached_words = []
        for word in words:
            if all(
                unicodedata.category(character).startswith("M")
                or "\u06d6" <= character <= "\u06ed"
                for character in word
            ):
                if attached_words:
                    attached_words[-1] += word
                else:
                    attached_words.append(word)
            else:
                attached_words.append(word)
        words = attached_words
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        w_len = font.getlength(t, direction="rtl", language=language)
        if cur and w_len > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines = lines + ([cur] if cur else [])
    if language == "ar":
        changed = True
        while len(lines) > 1 and changed:
            changed = False
            previous_words = lines[-2].split()
            current_words = lines[-1].split()
            if len(previous_words) <= 1:
                break
            candidate_previous = " ".join(previous_words[:-1])
            candidate_current = previous_words[-1] + " " + " ".join(current_words)
            if font.getlength(candidate_current, direction="rtl", language="ar") > maxw:
                break
            old_difference = abs(
                font.getlength(lines[-2], direction="rtl", language="ar")
                - font.getlength(lines[-1], direction="rtl", language="ar")
            )
            new_difference = abs(
                font.getlength(candidate_previous, direction="rtl", language="ar")
                - font.getlength(candidate_current, direction="rtl", language="ar")
            )
            if new_difference < old_difference:
                lines[-2:] = [candidate_previous, candidate_current]
                changed = True
    return lines

def wrap_english(text, font, maxw):
    lines, cur = [], ""
    for word in text.split():
        candidate = (cur + " " + word).strip()
        if cur and font.getlength(candidate, direction="ltr", language="en") > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = candidate
    return lines + ([cur] if cur else [])

def shaped_line_layout(
    lines,
    font,
    direction="rtl",
    language="ar",
    minimum_spacing=QURAN_ARABIC_LINE_SPACING,
    stroke=3,
):
    if not lines:
        return [], 0, 0
    bounds = [
        font.getbbox(
            line,
            anchor="ma",
            direction=direction,
            language=language,
            stroke_width=stroke,
        )
        for line in lines
    ]
    positions = [0]
    minimum_advance = int(round(font.size * minimum_spacing))
    for previous, current in zip(bounds, bounds[1:]):
        no_overlap_advance = previous[3] - current[1] + 2
        positions.append(positions[-1] + max(minimum_advance, no_overlap_advance))
    return positions, bounds[0][1], positions[-1] + bounds[-1][3]

def quran_arabic_line_layout(lines, font, stroke=3):
    return shaped_line_layout(lines, font, stroke=stroke)

def quran_arabic_text_height(lines, font, stroke=3):
    _, top, bottom = quran_arabic_line_layout(lines, font, stroke)
    return bottom - top

def put(d, xy, text, font, fill=(255, 255, 255, 255), stroke=3, direction="rtl", language="ur"):
    d.text(
        xy,
        text,
        font=font,
        fill=fill,
        anchor="ma",
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, 230),
        direction=direction,
        language=language,
    )

def normalize_surah_title(text):
    return text.replace("\u06c3", "\u0629")

def base_overlay(path, surah, label, show_bismillah, styles=None):
    styles = styles or {}
    scale = W / 1080
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    ov = ROOT / "public" / "assets" / "overlay.png"
    if ov.exists():
        im.alpha_composite(Image.open(ov).convert("RGBA").resize((W, H)))
    if show_bismillah:
        bismillah_color = styled_color(styles, "bismillah", (255, 255, 255, 255))
        bismillah_font = load_font(QURAN_ARABIC_FONT, int(round(64 * scale)))
        put(
            d, (W // 2, int(round(170 * scale))), QPC_BISMILLAH_TEXT, bismillah_font,
            bismillah_color, stroke=int(round(3 * scale)), language="ar",
        )
    surah_color = styled_color(styles, "surah", (255, 224, 140, 255))
    surah_font = load_font("amiri_bold", int(round(92 * scale)))
    put(
        d, (W // 2, int(round(330 * scale))), surah, surah_font,
        surah_color, int(round(3 * scale)), direction="rtl", language="ur",
    )
    label_color = styled_color(styles, "label", (255, 255, 255, 255))
    label_font = load_font("amiri_bold", int(round(54 * scale)))
    put(
        d, (W // 2, int(round(480 * scale))), label, label_font,
        label_color, int(round(3 * scale)), direction="rtl", language="ur",
    )
    channel_color = styled_color(styles, "channel", (255, 255, 255, 220))
    channel_font = load_font(styled_font(styles, "channel", "amiri_bold"), int(round(50 * scale)))
    put(
        d, (W // 2, H - int(round(220 * scale))), CHANNEL, channel_font,
        channel_color, int(round(2 * scale)), direction="ltr", language="en",
    )
    im.save(path)

def chunk_png(path, lines, font, spacing, base, is_arabic=False, color=None):
    scale = W / 1080
    im = Image.open(base).convert("RGBA"); d = ImageDraw.Draw(im)
    language = "ar" if is_arabic else "ur"
    text_positions, text_top, text_bottom = shaped_line_layout(
        lines,
        font,
        language=language,
        minimum_spacing=spacing,
        stroke=int(round(3 * scale)),
    )
    text_height = text_bottom - text_top
    box_h = text_height + int(round(90 * scale)) + 2
    cy = H // 2 + int(round(60 * scale))
    margin = int(round(50 * scale))
    radius = int(round(40 * scale))
    padding = int(round(45 * scale))
    
    d.rounded_rectangle((margin, cy - box_h // 2, W - margin, cy + box_h // 2), radius, fill=(0, 0, 0, 115))
    card_top = cy - box_h // 2
    card_bottom = card_top + box_h
    block_top = cy - text_height // 2
    first_line_y = block_top - text_top
    
    default_color = (255, 224, 140, 255) if is_arabic else (255, 255, 255, 255)
    fill_color = color or default_color
    
    text_boxes = []
    for index, ln in enumerate(lines):
        position_y = first_line_y + text_positions[index]
        bounds = font.getbbox(
            ln,
            anchor="ma",
            direction="rtl",
            language=language,
            stroke_width=int(round(3 * scale)),
        )
        text_boxes.append((position_y + bounds[1], position_y + bounds[3]))
        put(
            d, (W // 2, position_y), ln, font, fill=fill_color,
            stroke=int(round(3 * scale)), language="ar" if is_arabic else "ur",
        )
    if any(
        top < card_top + padding or bottom > card_bottom - padding
        for top, bottom in text_boxes
    ):
        raise ValueError("Rendered text exceeds its card bounds.")
        
    im.save(path)
    return {
        "card_bounds": (card_top, card_bottom),
        "text_bounds": text_boxes,
    }

def chunk_bilingual_png(path, arabic_lines, english_lines, arabic_font, english_font, base, styles):
    scale = W / 1080
    im = Image.open(base).convert("RGBA")
    draw = ImageDraw.Draw(im)
    arabic_spacing = QURAN_ARABIC_LINE_SPACING
    english_spacing = 1.25
    arabic_positions, arabic_top, arabic_bottom = quran_arabic_line_layout(
        arabic_lines, arabic_font, int(round(3 * scale))
    )
    arabic_block_height = arabic_bottom - arabic_top
    english_line_height = int(english_font.size * english_spacing)
    attribution = "Translation: Mohammed Marmaduke William Pickthall"
    attribution_font = load_font(
        styled_font(styles, "english", "amiri_regular"),
        max(14, int(round(english_font.size * 0.58))),
        "amiri_regular",
    )
    attribution_line_height = int(attribution_font.size * 1.3)
    padding = int(round(42 * scale))
    separator_gap = int(round(24 * scale))
    attribution_gap = int(round(12 * scale))
    text_height = (
        arabic_block_height
        + separator_gap
        + english_line_height * len(english_lines)
        + attribution_gap
        + attribution_line_height
    )
    box_height = text_height + 2 * padding
    center_y = H // 2 + int(round(60 * scale))
    margin = int(round(50 * scale))
    draw.rounded_rectangle(
        (margin, center_y - box_height // 2, W - margin, center_y + box_height // 2),
        int(round(40 * scale)),
        fill=(0, 0, 0, 125),
    )
    block_top = center_y - text_height // 2
    y = block_top - arabic_top
    arabic_color = styled_color(styles, "arabic", (255, 224, 140, 255))
    english_color = styled_color(styles, "english", (245, 247, 250, 255))
    for index, line in enumerate(arabic_lines):
        put(
            draw, (W // 2, y), line, arabic_font, arabic_color,
            stroke=int(round(3 * scale)), language="ar",
        )
        if index + 1 < len(arabic_lines):
            y += arabic_positions[index + 1] - arabic_positions[index]
    y = block_top + arabic_block_height + separator_gap
    for line in english_lines:
        put(
            draw, (W // 2, y), line, english_font, english_color,
            stroke=int(round(2 * scale)), direction="ltr", language="en",
        )
        y += english_line_height
    y += attribution_gap
    put(
        draw, (W // 2, y), attribution, attribution_font,
        styled_color(styles, "english", (200, 210, 220, 230)),
        stroke=int(round(scale)), direction="ltr", language="en",
    )
    im.save(path)

def render_ayah_image(arabic_text, english_text, reference, background_path, output_path):
    width, height = 1080, 1350
    image = ImageOps.fit(
        Image.open(background_path).convert("RGB"),
        (width, height),
        method=Image.Resampling.LANCZOS,
    ).convert("RGBA")
    image = Image.alpha_composite(
        image,
        Image.new("RGBA", image.size, (5, 10, 20, 112)),
    )
    draw = ImageDraw.Draw(image)
    reference_font = load_font("amiri_bold", 36)
    put(
        draw, (width // 2, 155), reference, reference_font,
        (255, 224, 140, 255), stroke=1, direction="ltr", language="en",
    )

    try:
        surah_number = int(reference.rsplit(" ", 1)[-1].split(":", 1)[0])
    except (ValueError, IndexError):
        raise ValueError("Ayah image reference must end with a surah:ayah number.")
    arabic_text = ayah_text_without_bismillah(arabic_text, surah_number)
    arabic_text = arabic_text.lstrip("\ufeff").strip()
    english_text = english_text.strip()
    max_width = width - 210
    text_area_height = 820
    fitted = None
    for arabic_size in range(82, 35, -2):
        arabic_font = load_font(QURAN_ARABIC_FONT, arabic_size)
        arabic_lines = wrap(arabic_text, arabic_font, max_width, language="ar")
        arabic_text_height = quran_arabic_text_height(
            arabic_lines, arabic_font, stroke=2
        )
        for english_size in range(34, 19, -1):
            english_font = load_font("amiri_regular", english_size)
            english_lines = wrap_english(english_text, english_font, max_width)
            english_line_height = int(english_size * 1.4)
            text_height = (
                arabic_text_height
                + 42
                + english_line_height * len(english_lines)
            )
            if text_height <= text_area_height:
                fitted = (
                    arabic_lines, english_lines, arabic_font, english_font,
                    arabic_text_height, english_line_height, text_height,
                )
                break
        if fitted:
            break
    if fitted is None:
        raise ValueError("Ayah text does not fit on the image.")

    (
        arabic_lines, english_lines, arabic_font, english_font,
        arabic_text_height, english_line_height, text_height,
    ) = fitted
    padding = 44
    card_height = text_height + 2 * padding
    center_y = 700
    card_top = center_y - card_height // 2
    card_bottom = center_y + (card_height + 1) // 2
    if card_top < 220 or card_bottom > 1180:
        raise ValueError("Ayah text does not fit on the image.")
    draw.rounded_rectangle(
        (62, card_top, width - 62, card_bottom),
        radius=34,
        fill=(4, 9, 18, 179),
        outline=(255, 224, 140, 95),
        width=2,
    )
    block_top = center_y - text_height // 2
    arabic_positions, arabic_top, _ = quran_arabic_line_layout(
        arabic_lines, arabic_font, stroke=2
    )
    y = block_top - arabic_top
    for index, line in enumerate(arabic_lines):
        put(
            draw, (width // 2, y), line, arabic_font,
            (255, 224, 140, 255), stroke=2, language="ar",
        )
        if index + 1 < len(arabic_lines):
            y += arabic_positions[index + 1] - arabic_positions[index]
    y = block_top + arabic_text_height + 42
    for line in english_lines:
        put(
            draw, (width // 2, y), line, english_font,
            (248, 250, 252, 255), stroke=1, direction="ltr", language="en",
        )
        y += english_line_height

    attribution_font = load_font("amiri_regular", 22)
    put(
        draw, (width // 2, 1215),
        "Translation: Mohammed Marmaduke William Pickthall",
        attribution_font, (235, 238, 242, 255),
        stroke=1, direction="ltr", language="en",
    )
    image.convert("RGB").save(output_path, format="PNG", optimize=True)

@functools.cache
def load_quran():
    return json.loads((ROOT / "data" / "quran.json").read_text(encoding="utf-8"))

def plan_duration(
    s,
    start,
    end,
    length_limit,
    content_mode,
    Q=None,
    reciter_id=None,
    urdu_voice_id=None,
):
    if content_mode not in {"full", "urdu_only", "arabic_only"}:
        raise ValueError("Content mode must be full, urdu_only, or arabic_only.")
    if Q is None:
        Q = load_quran()
    if str(s) not in Q["surahs"]:
        raise ValueError("Surah must be between 1 and 114.")
    verse_count = int(Q["surahs"][str(s)]["n"])
    if not 1 <= start <= end <= verse_count:
        raise ValueError(f"Ayah range must be between 1 and {verse_count} for this surah.")
    length_limit = min(max(float(length_limit), 1.0), 180.0)
    reciter_id = resolve_reciter_id("arabic", reciter_id)
    urdu_voice_id = resolve_reciter_id("urdu", urdu_voice_id)

    items = []
    for ayah in range(start, end + 1):
        audio_kind = "arabic" if content_mode == "arabic_only" else "urdu"
        audio_reciter_id = reciter_id if audio_kind == "arabic" else urdu_voice_id
        audio_seconds = cached_ayah_duration(s, ayah, audio_kind, audio_reciter_id)
        if audio_seconds is None:
            audio_seconds = measure_ayah_audio(s, ayah, audio_kind, audio_reciter_id)
        arabic_seconds = 0.0
        if content_mode == "full":
            arabic_seconds = cached_ayah_duration(s, ayah, "arabic", reciter_id)
            if arabic_seconds is None:
                arabic_seconds = measure_ayah_audio(s, ayah, "arabic", reciter_id)
        if audio_seconds is None or (content_mode == "full" and arabic_seconds is None):
            raise RuntimeError(f"Audio duration could not be measured for ayah {s}:{ayah}.")
        urdu_start = arabic_seconds + GAP_AFTER_ARABIC if content_mode == "full" else 0.0
        items.append({
            "ayah": ayah,
            "ar_dur": arabic_seconds,
            "u0": urdu_start,
            "ur_dur": audio_seconds if content_mode != "arabic_only" else 0.0,
            "len": arabic_seconds + GAP_AFTER_ARABIC + audio_seconds
            if content_mode == "full" else audio_seconds,
        })

    requested_seconds = sum(item["len"] for item in items)
    tolerance_limit = min(length_limit * (1 + DURATION_TOLERANCE), 180.0)
    fit_items, tolerance_items = [], []
    fit_seconds = tolerance_seconds = 0.0
    for item in items:
        seconds = item["len"]
        if not fit_items:
            fit_items.append(item)
            fit_seconds += seconds
        elif fit_seconds + seconds <= length_limit:
            fit_items.append(item)
            fit_seconds += seconds
        else:
            break
    for item in items:
        if tolerance_items and tolerance_seconds + item["len"] > tolerance_limit:
            break
        if not tolerance_items and item["len"] > tolerance_limit:
            break
        tolerance_items.append(item)
        tolerance_seconds += item["len"]

    return {
        "items": items,
        "fit_items": fit_items,
        "estimated_seconds": fit_seconds,
        "range_seconds": requested_seconds,
        "fits_up_to": fit_items[-1]["ayah"] if fit_items else start - 1,
        "fit_seconds": fit_seconds,
        "tolerance_fits_up_to": tolerance_items[-1]["ayah"] if tolerance_items else start - 1,
        "total_count": len(items),
    }

def plan_reel(
    Q, s, a0, a1, length_limit, content_mode, reciter_id=None, urdu_voice_id=None
):
    plan = plan_duration(
        s, a0, a1, length_limit, content_mode, Q, reciter_id, urdu_voice_id
    )
    items = []
    surah_data = Q["surahs"][str(s)]
    for item in plan["fit_items"]:
        a = item["ayah"]
        english_text = None
        if content_mode == "arabic_only":
            english_text = surah_data.get("english_ayahs", {}).get(str(a))
            if not isinstance(english_text, str) or not english_text.strip():
                raise RuntimeError(
                    "English Quran text is missing. Run `python fetch_assets.py` "
                    "to download the required translation."
                )
        clip = ayah_clip(s, a, content_mode, reciter_id, urdu_voice_id)
        if clip is None:
            raise RuntimeError(f"Audio could not be prepared for ayah {s}:{a}.")
        item.update(clip)
        ar_text = verified_arabic_text(s, a)
        if a == 1:
            ar_text = ayah_text_without_bismillah(ar_text, s)
        if content_mode == "arabic_only":
            item.update(text="", english=english_text, arabic=ar_text)
        else:
            ur_text = surah_data["ayahs"][str(a)]
            item.update(text=ur_text, arabic=ar_text)
        items.append(item)
    plan["items"] = items
    return plan

def build(
    Q, s, a0, a1, outfile, bgs, rng, tmp, styles=None,
    max_duration=MAX_DUR, content_mode="full", music_filename=None,
    reciter_id=None, urdu_voice_id=None,
):
    require_raqm()
    styles = styles or {}
    plan = plan_reel(
        Q, s, a0, a1, max_duration, content_mode, reciter_id, urdu_voice_id
    )
    items = plan["items"]
    total = plan["fit_seconds"]
    used_a1 = plan["fits_up_to"]
    warnings = []
    if used_a1 < a1:
        warnings.append(
            f"Range {a0}-{a1} thi, {max_duration}s mein sirf {a0}-{used_a1} fit hui."
        )
    if items[0]["len"] > max_duration:
        warnings.append(
            f"Ayat {a0} ki audio {total:.0f}s ki hai; poori ayat rakhi gayi."
        )
    scale = W / 1080
    arabic_font_key = QURAN_ARABIC_FONT
    urdu_font_key = resolve_font_name(styled_font(styles, "urdu", "nastaliq_regular"), "nastaliq_regular")
    english_font_key = resolve_font_name(
        styled_font(styles, "english", "amiri_regular"),
        "amiri_regular",
    )
    label_font_key = "amiri_bold"
    allowed = max(len(items), int(total / SECONDS_PER_CHUNK))
    font_sizes = [max(1, int(round(size * scale))) for size in FONT_SIZES]
    wrap_width = int(round(W - 200 * scale))
    ayah_label = f"آیت {urdu_digits(a0)}" if a0 == used_a1 else f"آیات {urdu_digits(a0)} تا {urdu_digits(used_a1)}"
    label_font = load_font(label_font_key, int(round(54 * scale)), "amiri_bold")
    label_draw = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    label_bounds = label_draw.textbbox(
        (W // 2, int(round(480 * scale))),
        ayah_label,
        font=label_font,
        anchor="ma",
        stroke_width=int(round(3 * scale)),
        direction="rtl",
        language="ur",
    )
    minimum_chunk_top = label_bounds[3] + int(round(20 * scale))
    chunk_center = H // 2 + int(round(60 * scale))
    chunk_padding = int(round(90 * scale))

    def chunks_clear_label(wrapped_items, font, spacing, is_arabic=False):
        for lines in wrapped_items:
            for start in range(0, len(lines), LINES_PER_CHUNK):
                group = lines[start:start + LINES_PER_CHUNK]
                _, text_top, text_bottom = shaped_line_layout(
                    group,
                    font,
                    language="ar" if is_arabic else "ur",
                    minimum_spacing=spacing,
                    stroke=int(round(3 * scale)),
                )
                text_height = text_bottom - text_top
                box_height = text_height + chunk_padding
                if chunk_center - box_height // 2 < minimum_chunk_top:
                    return False
        return True

    arabic_font = None
    arabic_spacing = QURAN_ARABIC_LINE_SPACING
    urdu_font = None
    wrapped_en = []
    if content_mode == "arabic_only":
        for size in font_sizes:
            candidate_arabic_font = load_font(arabic_font_key, size, arabic_font_key)
            candidate_english_font = load_font(
                english_font_key,
                max(24, int(round(size * 0.48))),
                english_font_key,
            )
            candidate_arabic = [
                wrap(item["arabic"], candidate_arabic_font, wrap_width, language="ar")
                for item in items
            ]
            candidate_english = [
                wrap_english(item["english"], candidate_english_font, wrap_width)
                for item in items
            ]
            candidate_groups = []
            for arabic_lines, english_lines in zip(candidate_arabic, candidate_english):
                group_count = max(
                    1,
                    -(-len(arabic_lines) // LINES_PER_CHUNK),
                    -(-len(english_lines) // LINES_PER_CHUNK),
                )
                for group_index in range(group_count):
                    start_ar = group_index * len(arabic_lines) // group_count
                    end_ar = (group_index + 1) * len(arabic_lines) // group_count
                    start_en = group_index * len(english_lines) // group_count
                    end_en = (group_index + 1) * len(english_lines) // group_count
                    candidate_groups.append((
                        arabic_lines[start_ar:end_ar],
                        english_lines[start_en:end_en],
                    ))
            bilingual_fits_below_label = all(
                chunk_center
                - (
                    quran_arabic_text_height(
                        arabic_group,
                        candidate_arabic_font,
                        int(round(3 * scale)),
                    )
                    + int(candidate_english_font.size * 1.25) * len(english_group)
                    + int(max(14, round(candidate_english_font.size * 0.58)) * 1.3)
                    + int(round(130 * scale))
                    + int(round(12 * scale))
                ) // 2
                >= minimum_chunk_top
                for arabic_group, english_group in candidate_groups
            )
            if len(candidate_groups) <= allowed and bilingual_fits_below_label:
                arabic_font = candidate_arabic_font
                english_font = candidate_english_font
                wrapped_ar = candidate_arabic
                wrapped_en = candidate_english
                break
        if arabic_font is None:
            raise SystemExit(
                "Arabic and English text cannot fit below the ayah label at the selected render resolution."
            )
    else:
        urdu_spacing = 1.8 if "nastaliq" in urdu_font_key else 1.45
        for size in font_sizes:
            candidate_font = load_font(urdu_font_key, size, urdu_font_key)
            candidate_wrapped = [wrap(item["text"], candidate_font, wrap_width) for item in items]
            chunks = sum(-(-len(lines) // LINES_PER_CHUNK) for lines in candidate_wrapped)
            if chunks <= allowed and chunks_clear_label(candidate_wrapped, candidate_font, urdu_spacing):
                urdu_font = candidate_font
                wrapped_ur = candidate_wrapped
                break
        if urdu_font is None:
            raise SystemExit("Urdu text cannot fit below the ayah label at the selected render resolution.")

        for size in font_sizes:
            candidate_font = load_font(arabic_font_key, size, arabic_font_key)
            candidate_wrapped = [
                wrap(item["arabic"], candidate_font, wrap_width, language="ar")
                if item.get("arabic") else []
                for item in items
            ]
            chunks = sum(-(-len(lines) // LINES_PER_CHUNK) for lines in candidate_wrapped)
            if chunks <= allowed and chunks_clear_label(
                candidate_wrapped, candidate_font, arabic_spacing, is_arabic=True
            ):
                arabic_font = candidate_font
                wrapped_ar = candidate_wrapped
                break
        if arabic_font is None:
            raise SystemExit("Arabic text cannot fit below the ayah label at the selected render resolution.")

    timeline, off = [], 0.0
    if content_mode == "arabic_only":
        for item, arabic_lines, english_lines in zip(items, wrapped_ar, wrapped_en):
            group_count = max(
                1,
                -(-len(arabic_lines) // LINES_PER_CHUNK),
                -(-len(english_lines) // LINES_PER_CHUNK),
            )
            for group_number in range(group_count):
                start_time = off + item["len"] * group_number / group_count
                end_time = off + item["len"] * (group_number + 1) / group_count
                start_ar = group_number * len(arabic_lines) // group_count
                end_ar = (group_number + 1) * len(arabic_lines) // group_count
                start_en = group_number * len(english_lines) // group_count
                end_en = (group_number + 1) * len(english_lines) // group_count
                timeline.append((
                    start_time, end_time,
                    arabic_lines[start_ar:end_ar], True,
                    english_lines[start_en:end_en],
                ))
            off += item["len"]
    else:
        for it, u_lines, ar_lines in zip(items, wrapped_ur, wrapped_ar):
            d_i = it["len"]
            if INCLUDE_ARABIC_AUDIO and ar_lines:
                ar_groups = [
                    ar_lines[k:k + LINES_PER_CHUNK]
                    for k in range(0, len(ar_lines), LINES_PER_CHUNK)
                ]
                ar_span = it["u0"]
                weights_ar = [sum(len(x) for x in group) for group in ar_groups]
                t_ar = off
                for group_index, group in enumerate(ar_groups):
                    st_t = t_ar
                    t_ar += ar_span * weights_ar[group_index] / sum(weights_ar)
                    timeline.append((st_t, t_ar, group, True, None))

            u_groups = [
                u_lines[k:k + LINES_PER_CHUNK]
                for k in range(0, len(u_lines), LINES_PER_CHUNK)
            ]
            u_span = d_i - it["u0"]
            weights_u = [sum(len(x) for x in group) for group in u_groups]
            t_u = off + it["u0"]
            for group_index, group in enumerate(u_groups):
                st_t = t_u
                t_u += u_span * weights_u[group_index] / sum(weights_u)
                timeline.append((st_t, t_u, group, False, None))
            off += d_i

    S = Q["surahs"][str(s)]
    used_label = f"آیت {urdu_digits(a0)}" if a0 == used_a1 else f"آیات {urdu_digits(a0)} تا {urdu_digits(used_a1)}"
    base_p = tmp / "base.png"; base_overlay(base_p, normalize_surah_title("سورۃ " + strip_harakat_surah(S["name"]).replace("سورة", "").strip()), used_label, s != 9, styles)
    lst = tmp / "list.txt"; rows = []
    
    for k, (st, en, lines_group, is_arabic, english_group) in enumerate(timeline):
        p = tmp / f"c{k}.png"
        if english_group is not None:
            chunk_bilingual_png(
                p, lines_group, english_group, arabic_font, english_font, base_p, styles
            )
        else:
            font = arabic_font if is_arabic else urdu_font
            spacing = arabic_spacing if is_arabic else urdu_spacing
            element = "arabic" if is_arabic else "urdu"
            default_color = (255, 224, 140, 255) if is_arabic else (255, 255, 255, 255)
            color = styled_color(styles, element, default_color)
            chunk_png(p, lines_group, font, spacing, base_p, is_arabic=is_arabic, color=color)
        rows.append(f"file '{p.resolve().as_posix()}'\nduration {en - st:.3f}")
        
    rows.append(f"file '{(tmp / f'c{len(timeline)-1}.png').resolve().as_posix()}'")
    lst.write_text("\n".join(rows))
    
    playlist = background_playlist(bgs, total, rng)
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-v", "error",
        *FFMPEG_FILTER_THREADS,
    ]
    for path, seek, segment_duration in playlist:
        cmd += [
            *FFMPEG_CODEC_THREADS,
            "-stream_loop", "-1", "-ss", f"{seek:.3f}",
            "-t", f"{segment_duration:.3f}", "-i", str(path),
        ]
    overlay_input = len(playlist)
    cmd += [*FFMPEG_CODEC_THREADS, "-f", "concat", "-safe", "0", "-i", str(lst)]

    fc = []
    background_labels = []
    for idx, (_, _, segment_duration) in enumerate(playlist):
        background_label = f"bg{idx}"
        fc.append(
            f"[{idx}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},eq=brightness={BRIGHTNESS},setsar=1,format=yuv420p,settb=AVTB,"
            f"tpad=stop_mode=clone:stop_duration=1,trim=duration={segment_duration:.3f},"
            f"setpts=PTS-STARTPTS[{background_label}]"
        )
        background_labels.append(f"[{background_label}]")
    if len(playlist) == 1:
        fc.append(f"{background_labels[0]}null[vbg]")
    else:
        previous_label = background_labels[0]
        elapsed = playlist[0][2]
        for idx in range(1, len(playlist)):
            mixed_label = f"bgmix{idx}"
            offset = elapsed - 0.5
            fc.append(
                f"{previous_label}{background_labels[idx]}"
                f"xfade=transition=fade:duration=0.5:offset={max(0, offset):.3f}[{mixed_label}]"
            )
            previous_label = f"[{mixed_label}]"
            elapsed += playlist[idx][2] - 0.5
        fc.append(f"{previous_label}null[vbg]")
    fc += [f"[{overlay_input}:v]format=rgba[ov]", "[vbg][ov]overlay=shortest=1[v1]",
          f"[v1]fade=t=in:d=0.6,fade=t=out:st={total-0.7:.2f}:d=0.7[vout]"]
    
    idx, labels = overlay_input + 1, []
    norm = ",loudnorm=I=-18:TP=-2:LRA=7" if NORMALIZE_AUDIO else ""
    # Each loudnorm instance holds ~50MB; low profile normalizes once after concat instead of per segment.
    low_mem = os.getenv("RENDER_MEMORY_PROFILE", "").lower() == "low"
    seg_norm = "" if low_mem else norm
    for it in items:
        for f, d_seg, pad in it["segs"]:
            cmd += [*FFMPEG_CODEC_THREADS, "-i", f]
            fc.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo{seg_norm},"
                      f"asetpts=PTS-STARTPTS" + (f",apad=pad_dur={pad}" if pad else "") + f"[s{idx}]")
            labels.append(f"[s{idx}]"); idx += 1
            
    # Check if background music file is provided and exists
    music_path = None
    if music_filename and music_filename != "None" and music_filename != "none":
        candidate_path = BACKGROUND_DIR / "music" / music_filename
        if candidate_path.exists():
            music_path = candidate_path

    music_input_index = None
    if music_path:
        cmd += [*FFMPEG_CODEC_THREADS, "-stream_loop", "-1", "-i", str(music_path)]
        music_input_index = idx
        idx += 1

    # Concatenate main Quran recitation audio segments
    fc.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1" + (f"{norm}[rec_audio]" if low_mem else "[rec_audio]"))

    # Mix background music softly if present, otherwise just output recitation audio with fades
    if music_input_index is not None:
        fc.append(
            f"[{music_input_index}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume=0.12,atrim=duration={total:.2f},asetpts=PTS-STARTPTS[bgm]"
        )
        fc.append(
            f"[rec_audio][bgm]amix=inputs=2:duration=first:dropout_transition=2,"
            f"afade=t=in:d=0.2,afade=t=out:st={total-0.5:.2f}:d=0.5[aout]"
        )
    else:
        fc.append(f"[rec_audio]afade=t=in:d=0.2,afade=t=out:st={total-0.5:.2f}:d=0.5[aout]")

    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.2f}"]
    try:
        encode_video(cmd, outfile)
    except subprocess.CalledProcessError:
        if len(playlist) < 2:
            raise
        print("Background xfade failed; retrying with hard cuts.", file=sys.stderr)
        fc = [entry for entry in fc if "xfade=transition=" not in entry]
        fc = [entry for entry in fc if not re.search(r"\[bgmix\d+\]null\[vbg\]", entry)]
        fc.append("".join(background_labels) + f"concat=n={len(background_labels)}:v=1:a=0[vbg]")
        fallback_cmd = cmd[:cmd.index("-filter_complex")]
        fallback_cmd += ["-filter_complex", ";".join(fc)] + cmd[cmd.index("-map"):]
        encode_video(fallback_cmd, outfile)
    return S, total, used_a1, warnings

def parse_picks(f):
    out = []
    for ln in Path(f).read_text().splitlines():
        m = re.match(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?", ln)
        if m: out.append((int(m[1]), int(m[2]), int(m[3] or m[2])))
    return out

def pick_random(Q, count, rng, min_dur, max_dur):
    used_f = CACHE_DIR / "used.json"; used = set(json.loads(used_f.read_text())) if used_f.exists() else set()
    ns = [Q["surahs"][str(s)]["n"] for s in range(1, 115)]
    picks, tries = [], 0
    while len(picks) < count and tries < 3000:
        tries += 1
        s = rng.choices(range(1, 115), weights=ns)[0]; n = ns[s - 1]; a0 = rng.randint(1, n)
        a, t, ok, selected_end = a0, 0.0, True, a0 - 1
        while a <= n and t < min_dur:
            if f"{s}:{a}" in used: ok = False; break
            plan = plan_duration(
                s,
                a0,
                a,
                max_dur,
                CONTENT_MODE,
                Q,
            )
            selected_end = plan["fits_up_to"]
            t = plan["fit_seconds"]
            if selected_end < a:
                break
            a = selected_end + 1
            if t > max_dur:
                break
        a1 = selected_end
        if ok and a1 >= a0 and (t >= min_dur or (a1 == n and t >= min_dur * .7)):
            picks.append((s, a0, a1)); used.update(f"{s}:{x}" for x in range(a0, a1 + 1))
            print(f"   chuna: {s}:{a0}-{a1} ({t:.0f}s)")
    used_f.parent.mkdir(exist_ok=True); used_f.write_text(json.dumps(sorted(used)))
    return picks

def ensure_required_tools():
    missing = [cmd for cmd in ("ffmpeg", "ffprobe") if shutil.which(cmd) is None]
    if missing:
        sys.exit(
            "Missing required media tools: " + ", ".join(missing) + ". "
            "Install FFmpeg and ensure ffmpeg/ffprobe are available on PATH. "
            "Windows: winget install Gyan.Dev.FFmpeg or choco install ffmpeg"
        )


def main():
    global INCLUDE_ARABIC_AUDIO, CONTENT_MODE, CURRENT_RECITER_ID, CURRENT_URDU_VOICE_ID
    ensure_required_tools()
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks")
    ap.add_argument("--pick", help="Direct single pick, e.g. 18:9-15")
    ap.add_argument("--random", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--min-dur", type=int, default=MIN_DUR)
    ap.add_argument("--max-dur", type=int, default=MAX_DUR)
    ap.add_argument("--quality", choices=tuple(X264_PRESETS), default="high")
    content_group = ap.add_mutually_exclusive_group()
    content_group.add_argument(
        "--urdu-only", action="store_true",
        help="Use Urdu translation audio and text only.",
    )
    content_group.add_argument(
        "--arabic-only", action="store_true",
        help="Use Arabic recitation with Arabic text and English translation.",
    )
    ap.add_argument("--batch-id")
    ap.add_argument("--work-dir")
    ap.add_argument("--result-json")
    ap.add_argument("--style-json")
    ap.add_argument("--check", nargs="*", type=int)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--music", help="Background music filename from backgrounds/music/")
    ap.add_argument("--reciter", default=DEFAULT_ARABIC_RECITER_ID)
    ap.add_argument("--urdu-voice", default=DEFAULT_URDU_VOICE_ID)
    a = ap.parse_args()
    a.max_dur = min(max(1, a.max_dur), 180)
    a.min_dur = min(max(1, a.min_dur), a.max_dur)
    styles = load_video_styles(a.style_json)
    CONTENT_MODE = "arabic_only" if a.arabic_only else "urdu_only" if a.urdu_only else "full"
    INCLUDE_ARABIC_AUDIO = CONTENT_MODE != "urdu_only"
    try:
        CURRENT_RECITER_ID = resolve_reciter_id("arabic", a.reciter)
        if CONTENT_MODE != "arabic_only":
            CURRENT_URDU_VOICE_ID = resolve_reciter_id("urdu", a.urdu_voice)
    except ValueError as exc:
        ap.error(str(exc))
    set_video_quality(a.quality)
    
    qf = ROOT / "data/quran.json"
    if not qf.exists(): sys.exit("Pehle chalao: python fetch_assets.py")
    Q = json.loads(qf.read_text(encoding="utf-8")); rng = random.Random(a.seed)
    
    if a.check is not None:
        for s in a.check:
            n = Q["surahs"][str(s)]["n"]; got = [
                ayah_clip(
                    s, x, reciter_id=CURRENT_RECITER_ID,
                    urdu_voice_id=CURRENT_URDU_VOICE_ID,
                )
                for x in range(1, min(n, 5) + 1)
            ]
            ok = sum(1 for c in got if c)
            print(f"Surah {s}: pehli {len(got)} ayaat mein se {ok} ki audio mili", end="")
            print("  [" + ", ".join(f"{c['len']:.1f}s" for c in got if c) + "]" if ok else "  -> internet/link check karo")
        return

    if not a.dry:
        require_raqm()
        
    if a.pick:
        m = re.match(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?", a.pick)
        picks = [(int(m[1]), int(m[2]), int(m[3] or m[2]))] if m else []
    else:
        picks = parse_picks(a.picks) if a.picks else pick_random(Q, a.random, rng, a.min_dur, a.max_dur) if a.random else sys.exit("--picks, --pick ya --random do")
    background_files = [p for d in (BACKGROUND_DIR, CLIP_CACHE_DIR) for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mov")]
    generated_reels = [p for p in background_files if is_generated_reel(p)]
    bgs = [p for p in background_files if not is_generated_reel(p)]
    if generated_reels:
        print(
            "Ignoring generated reel(s) in backgrounds/: "
            + ", ".join(path.name for path in generated_reels),
            file=sys.stderr,
        )
    if not bgs:
        still = still_background()
        if not still: sys.exit("No background clips (backgrounds/ or Supabase) and no backgrounds/images/ still.")
        bgs = [still]
    out = OUTPUT_DIR; out.mkdir(exist_ok=True, parents=True)
    tmp = Path(a.work_dir) if a.work_dir else CACHE_DIR / "tmp"
    tmp.mkdir(exist_ok=True, parents=True)
    batch_id = re.sub(r"[^A-Za-z0-9_-]", "", a.batch_id or "")
    capf = out / "captions.csv"; newcap = not capf.exists()

    if a.dry:
        for i, (s, a0, a1) in enumerate(picks, 1):
            print(f"[{i}/{len(picks)}] Surah {s}:{a0}-{a1} [{a.quality}]")
        return
    
    generated = []
    result_path = Path(a.result_json) if a.result_json else None
    with open(capf, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); newcap and w.writerow(["file", "caption"])
        for i, (s, a0, a1) in enumerate(picks, 1):
            prefix = f"{batch_id}_" if batch_id else ""
            name = f"{prefix}{i:03d}_s{s}_{a0}-{a1}.mp4"
            outfile = out / name
            suffix = 1
            while outfile.exists():
                outfile = out / f"{Path(name).stem}_{suffix}.mp4"
                suffix += 1
            name = outfile.name
            print(f"[{i}/{len(picks)}] Surah {s}:{a0}-{a1}", end=" ")
            content_mode = CONTENT_MODE
            res = build(
                Q, s, a0, a1, outfile, bgs, rng, tmp, styles,
                a.max_dur, content_mode, a.music,
                CURRENT_RECITER_ID, CURRENT_URDU_VOICE_ID,
            )
            if res is None: print("SKIP (audio download nahi hui)"); continue
            S, total, used_a1, warnings = res
            print(f"-> {name} ({total:.0f}s)")
            caption = f"Surah {S['english']} ({s}) | Ayat {a0}" + (f"-{used_a1}" if used_a1 != a0 else "") + f"\n#Quran #Islam #{S['english'].replace(' ', '').replace('-', '')}"
            w.writerow([name, caption])
            generated.append({
                "filename": name,
                "caption": caption,
                "surah": s,
                "start_ayah": a0,
                "end_ayah": used_a1,
                "duration": round(total, 2),
                "warnings": warnings,
            })
            if result_path:
                result_path.parent.mkdir(exist_ok=True, parents=True)
                temp_result_path = result_path.with_suffix(result_path.suffix + ".tmp")
                temp_result_path.write_text(json.dumps(generated, ensure_ascii=False), encoding="utf-8")
                temp_result_path.replace(result_path)

if __name__ == "__main__": main()