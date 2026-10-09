#!/usr/bin/env python3
"""Noor ul Quran auto reel maker (Arabic tilawat ke waqt Arabic, Urdu audio ke waqt Urdu text).
   python make.py --random 1                      (Standard ~1 min reel)
   python make.py --picks picks.txt --max-dur 90  (Waqia/Long Reel up to 1m 30s)
"""
import argparse, csv, json, os, random, re, shutil, subprocess, sys, time
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, features
import requests

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_BIDI = True
except ImportError:
    HAS_BIDI = False

# ---------------- DEFAULT SETTINGS ----------------
W, H, FPS = 1080, 1920, 30
MIN_DUR, MAX_DUR = 20, 60          # Default reel length (20s to 60s / 1 min)
CHANNEL = "Noor ul Quran"
SECONDS_PER_CHUNK = 3.0
LINES_PER_CHUNK = 3
FONT_SIZES = [72, 64, 56, 48, 42]
BRIGHTNESS = -0.18
INCLUDE_ARABIC_AUDIO = True
GAP_AFTER_ARABIC = 0.35
NORMALIZE_AUDIO = True
BASE_URL = "https://everyayah.com/data/"
ARABIC_FOLDER = "Alafasy_128kbps"
URDU_FOLDER = "translations/urdu_shamshad_ali_khan_46kbps"
BISMILLAH = "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"
# --------------------------------------------------
ROOT = Path(__file__).parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", ROOT / "output"))
BACKGROUND_DIR = Path(os.getenv("BACKGROUNDS_DIR", ROOT / "backgrounds"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", ROOT / "cache"))
ADMIN_CONTENT_FILE = ROOT / "data" / "admin_content.json"
ADMIN_AUDIO_DIR = ROOT / "audio" / "admin"
_admin_content = None
_hardware_encoder = None

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)

def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

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

def encode_video(cmd, outfile):
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
        result = subprocess.run(cmd + hardware_args + ["-pix_fmt", "yuv420p"] + audio_args, check=False)
        if result.returncode == 0:
            return
        print(f"Hardware encoder {encoder} failed; retrying with CPU encoding.", file=sys.stderr)
        Path(outfile).unlink(missing_ok=True)

    run(
        cmd
        + ["-c:v", "libx264", "-preset", "superfast", "-crf", "22", "-pix_fmt", "yuv420p"]
        + audio_args
    )

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

    playlist, remaining, previous = [], total, None
    durations = {path: dur(path) for path in unique}
    while remaining > 0.001:
        choices = [path for path in unique if path != previous]
        path = rng.choice(choices or unique)
        clip_duration = durations[path]
        segment_duration = min(clip_duration, remaining)
        if segment_duration <= 0:
            raise RuntimeError(f"Background video has invalid duration: {path}")
        seek = rng.uniform(0, clip_duration - segment_duration) if clip_duration > segment_duration else 0
        playlist.append((path, seek, segment_duration))
        remaining -= segment_duration
        previous = path
    return playlist

def urdu_digits(n): return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
def strip_harakat_surah(s): return re.sub("[\u064B-\u065F\u0670\u06D6-\u06ED]", "", s)

def remove_leading_bismillah(text):
    words = text.split()
    expected = ["بسم", "الله", "الرحمن", "الرحيم"]
    actual = [strip_harakat_surah(word).replace("ٱ", "ا") for word in words[:4]]
    return " ".join(words[4:]) if actual == expected else text

def admin_content():
    global _admin_content
    if _admin_content is None:
        if ADMIN_CONTENT_FILE.exists():
            _admin_content = json.loads(ADMIN_CONTENT_FILE.read_text(encoding="utf-8"))
        else:
            _admin_content = {"verses": {}}
    return _admin_content

def fetch_ayah(kind, s, a):
    verse = admin_content()["verses"].get(f"{s}:{a}", {})
    custom_extension = verse.get("audio", {}).get(kind)
    if custom_extension:
        custom_path = ADMIN_AUDIO_DIR / f"{s:03d}{a:03d}_{kind}.{custom_extension}"
        return custom_path if custom_path.exists() and custom_path.stat().st_size > 1000 else None

    p = ROOT / "audio" / kind / f"{s:03d}{a:03d}.mp3"
    if p.exists() and p.stat().st_size > 1000: return p
    folder = ARABIC_FOLDER if kind == "arabic" else URDU_FOLDER
    url = f"{BASE_URL}{folder}/{s:03d}{a:03d}.mp3"
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 1000:
                p.write_bytes(r.content); return p
            if r.status_code == 404: return None
        except requests.RequestException:
            time.sleep(2)
    return None

_clip_cache = {}
def ayah_clip(s, a):
    if (s, a) in _clip_cache: return _clip_cache[(s, a)]
    ur = fetch_ayah("urdu", s, a); ar = fetch_ayah("arabic", s, a) if INCLUDE_ARABIC_AUDIO else None
    if ur is None or (INCLUDE_ARABIC_AUDIO and ar is None):
        c = None
    else:
        segs, u0 = [], 0.0
        da = 0.0
        if ar: 
            da = dur(ar)
            segs.append((str(ar), da, GAP_AFTER_ARABIC))
            u0 = da + GAP_AFTER_ARABIC
        du = dur(ur)
        segs.append((str(ur), du, 0.0))
        c = {"segs": segs, "ar_dur": da, "u0": u0, "ur_dur": du, "len": u0 + du}
    _clip_cache[(s, a)] = c
    return c

def find_font():
    fs = sorted(p for p in (ROOT / "fonts").glob("*") if p.suffix.lower() in (".ttf", ".otf"))
    if not fs: sys.exit("fonts/ folder mein font daalo")
    return fs[0]

def shape_text(text):
    if not features.check("raqm") and HAS_BIDI:
        return get_display(arabic_reshaper.reshape(text))
    return text

def wrap(text, font, maxw):
    lines, cur = [], ""
    use_raqm = features.check("raqm")
    for w in text.split():
        t = (cur + " " + w).strip()
        st = shape_text(t)
        w_len = font.getlength(st, direction="rtl", language="ur") if use_raqm else font.getlength(st)
        if cur and w_len > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])

def put(d, xy, text, font, fill=(255, 255, 255, 255), stroke=3):
    use_raqm = features.check("raqm")
    st = shape_text(text)
    kw = {"direction": "rtl", "language": "ur"} if use_raqm else {}
    d.text(xy, st, font=font, fill=fill, anchor="ma", stroke_width=stroke, stroke_fill=(0, 0, 0, 230), **kw)

def base_overlay(path, surah, label, show_bismillah, fpath):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    ov = ROOT / "assets/overlay.png"
    if ov.exists():
        im.alpha_composite(Image.open(ov).convert("RGBA").resize((W, H)))
    else:
        if show_bismillah: put(d, (W // 2, 170), BISMILLAH, ImageFont.truetype(str(fpath), 64))
        put(d, (W // 2, 330), surah, ImageFont.truetype(str(fpath), 92), (255, 224, 140, 255))
        put(d, (W // 2, 480), label, ImageFont.truetype(str(fpath), 54))
        put(d, (W // 2, H - 220), CHANNEL, ImageFont.truetype(str(fpath), 50), (255, 255, 255, 220), 2)
    im.save(path)

def chunk_png(path, lines, font, spacing, base, is_arabic=False):
    im = Image.open(base).convert("RGBA"); d = ImageDraw.Draw(im)
    lh = int(font.size * spacing)
    box_h = lh * len(lines) + 90
    cy = H // 2 + 60
    
    d.rounded_rectangle((50, cy - box_h // 2, W - 50, cy + box_h // 2), 40, fill=(0, 0, 0, 115))
    y = cy - box_h // 2 + 45
    
    fill_color = (255, 224, 140, 255) if is_arabic else (255, 255, 255, 255)
    
    for ln in lines:
        put(d, (W // 2, y), ln, font, fill=fill_color)
        y += lh
        
    im.save(path)

def plan_reel(Q, s, a0, a1):
    items = []
    surah_data = Q["surahs"][str(s)]
    audio_tasks = [
        (kind, s, ayah)
        for ayah in range(a0, a1 + 1)
        for kind in (("arabic", "urdu") if INCLUDE_ARABIC_AUDIO else ("urdu",))
    ]
    if audio_tasks:
        with ThreadPoolExecutor(max_workers=min(8, len(audio_tasks))) as executor:
            list(executor.map(lambda task: fetch_ayah(*task), audio_tasks))

    for a in range(a0, a1 + 1):
        c = ayah_clip(s, a)
        if c is None: return None
        override = admin_content()["verses"].get(f"{s}:{a}", {})
        ar_text = override.get("arabic_text") or surah_data.get("arabic", {}).get(str(a), "")
        if a == 1 and s != 9:
            ar_text = remove_leading_bismillah(ar_text)
        ur_text = override.get("urdu_text") or surah_data["ayahs"][str(a)]
        items.append(dict(c, ayah=a, text=ur_text, arabic=ar_text))
    return items

def build(Q, s, a0, a1, outfile, bgs, rng, tmp):
    items = plan_reel(Q, s, a0, a1)
    if items is None: return None
    total = sum(i["len"] for i in items)
    fpath = find_font(); spacing = 1.8 if "nastaliq" in fpath.name.lower() else 1.45
    allowed = max(len(items), int(total / SECONDS_PER_CHUNK))
    
    for size in FONT_SIZES:
        font = ImageFont.truetype(str(fpath), size)
        wrapped_ur = [wrap(i["text"], font, W - 200) for i in items]
        wrapped_ar = [wrap(i["arabic"], font, W - 200) if i.get("arabic") else [] for i in items]
        chunks = sum(-(-len(w) // LINES_PER_CHUNK) for w in wrapped_ur)
        if chunks <= allowed: break
        
    timeline, off = [], 0.0
    for it, u_lines, ar_lines in zip(items, wrapped_ur, wrapped_ar):
        d_i = it["len"]
        
        # 1. Arabic Tilawat Duration (Arabic Text Display)
        if INCLUDE_ARABIC_AUDIO and ar_lines:
            ar_groups = [ar_lines[k:k + LINES_PER_CHUNK] for k in range(0, len(ar_lines), LINES_PER_CHUNK)]
            ar_span = it["u0"]
            weights_ar = [sum(len(x) for x in g) for g in ar_groups]
            t_ar = off
            for j, g in enumerate(ar_groups):
                st_t = t_ar
                t_ar += ar_span * weights_ar[j] / sum(weights_ar)
                timeline.append((st_t, t_ar, g, True))
                
        # 2. Urdu Translation Duration (Urdu Text Display)
        u_groups = [u_lines[k:k + LINES_PER_CHUNK] for k in range(0, len(u_lines), LINES_PER_CHUNK)]
        u_span = d_i - it["u0"]
        weights_u = [sum(len(x) for x in g) for g in u_groups]
        t_u = off + it["u0"]
        for j, g in enumerate(u_groups):
            st_t = t_u
            t_u += u_span * weights_u[j] / sum(weights_u)
            timeline.append((st_t, t_u, g, False))
            
        off += d_i

    S = Q["surahs"][str(s)]
    label = f"آیت {urdu_digits(a0)}" if a0 == a1 else f"آیات {urdu_digits(a0)} تا {urdu_digits(a1)}"
    base_p = tmp / "base.png"; base_overlay(base_p, "سورۃ " + strip_harakat_surah(S["name"]).replace("سورة", "").strip(), label, s != 9, fpath)
    lst = tmp / "list.txt"; rows = []
    
    for k, (st, en, lines_group, is_arabic) in enumerate(timeline):
        p = tmp / f"c{k}.png"
        chunk_png(p, lines_group, font, spacing, base_p, is_arabic=is_arabic)
        rows.append(f"file '{p.resolve().as_posix()}'\nduration {en - st:.3f}")
        
    rows.append(f"file '{(tmp / f'c{len(timeline)-1}.png').resolve().as_posix()}'")
    lst.write_text("\n".join(rows))
    
    playlist = background_playlist(bgs, total, rng)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    for path, seek, segment_duration in playlist:
        cmd += ["-ss", f"{seek:.3f}", "-t", f"{segment_duration:.3f}", "-i", str(path)]
    overlay_input = len(playlist)
    cmd += ["-f", "concat", "-safe", "0", "-i", str(lst)]

    fc = []
    background_labels = []
    for idx, (_, _, segment_duration) in enumerate(playlist):
        label = f"bg{idx}"
        fc.append(
            f"[{idx}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},eq=brightness={BRIGHTNESS},setsar=1,"
            f"tpad=stop_mode=clone:stop_duration=1,trim=duration={segment_duration:.3f},"
            f"setpts=PTS-STARTPTS[{label}]"
        )
        background_labels.append(f"[{label}]")
    fc.append(
        "".join(background_labels) + f"concat=n={len(playlist)}:v=1:a=0[vbg]"
    )
    fc += [f"[{overlay_input}:v]fps={FPS},format=rgba[ov]", "[vbg][ov]overlay=shortest=1[v1]",
          f"[v1]fade=t=in:d=0.6,fade=t=out:st={total-0.7:.2f}:d=0.7[vout]"]
    idx, labels = overlay_input + 1, []
    norm = ",loudnorm=I=-18:TP=-2:LRA=7" if NORMALIZE_AUDIO else ""
    for it in items:
        for f, d_seg, pad in it["segs"]:
            cmd += ["-i", f]
            fc.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo{norm},"
                      f"asetpts=PTS-STARTPTS" + (f",apad=pad_dur={pad}" if pad else "") + f"[s{idx}]")
            labels.append(f"[s{idx}]"); idx += 1
    fc.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1,afade=t=in:d=0.2,afade=t=out:st={total-0.5:.2f}:d=0.5[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.2f}"]
    encode_video(cmd, outfile)
    return S, total

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
        a, t, ok = a0, 0.0, True
        while a <= n and t < min_dur:
            if f"{s}:{a}" in used: ok = False; break
            c = ayah_clip(s, a)
            if c is None: ok = False; break
            t += c["len"]; a += 1
            if t > max_dur: ok = False; break
        a1 = a - 1
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
    if not features.check("raqm") and not HAS_BIDI:
        sys.exit(
            "Arabic text support missing. Activate the project venv and run: "
            "pip install -r requirements.txt"
        )
    ensure_required_tools()
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks")
    ap.add_argument("--pick", help="Direct single pick, e.g. 18:9-15")
    ap.add_argument("--random", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--min-dur", type=int, default=MIN_DUR)
    ap.add_argument("--max-dur", type=int, default=MAX_DUR)
    ap.add_argument("--check", nargs="*", type=int)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    
    qf = ROOT / "data/quran.json"
    if not qf.exists(): sys.exit("Pehle chalao: python fetch_assets.py")
    Q = json.loads(qf.read_text(encoding="utf-8")); rng = random.Random(a.seed)
    
    if a.check is not None:
        for s in a.check:
            n = Q["surahs"][str(s)]["n"]; got = [ayah_clip(s, x) for x in range(1, min(n, 5) + 1)]
            ok = sum(1 for c in got if c)
            print(f"Surah {s}: pehli {len(got)} ayaat mein se {ok} ki audio mili", end="")
            print("  [" + ", ".join(f"{c['len']:.1f}s" for c in got if c) + "]" if ok else "  -> internet/link check karo")
        return
        
    if a.pick:
        m = re.match(r"\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?", a.pick)
        picks = [(int(m[1]), int(m[2]), int(m[3] or m[2]))] if m else []
    else:
        picks = parse_picks(a.picks) if a.picks else pick_random(Q, a.random, rng, a.min_dur, a.max_dur) if a.random else sys.exit("--picks, --pick ya --random do")
    bgs = [p for p in BACKGROUND_DIR.glob("*") if p.suffix.lower() in (".mp4", ".mov")]
    if not bgs: sys.exit("backgrounds/ folder mein mp4 clips daalo")
    out = OUTPUT_DIR; out.mkdir(exist_ok=True, parents=True); tmp = CACHE_DIR / "tmp"; tmp.mkdir(exist_ok=True, parents=True)
    capf = out / "captions.csv"; newcap = not capf.exists()
    
    with open(capf, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); newcap and w.writerow(["file", "caption"])
        for i, (s, a0, a1) in enumerate(picks, 1):
            name = f"{i:03d}_s{s}_{a0}-{a1}.mp4"; print(f"[{i}/{len(picks)}] Surah {s}:{a0}-{a1}", end=" ")
            if a.dry: print(); continue
            res = build(Q, s, a0, a1, out / name, bgs, rng, tmp)
            if res is None: print("SKIP (audio download nahi hui)"); continue
            S, total = res
            print(f"-> {name} ({total:.0f}s)")
            w.writerow([name, f"Surah {S['english']} ({s}) | Ayat {a0}" + (f"-{a1}" if a1 != a0 else "") + f"\n#Quran #Islam #{S['english'].replace(' ', '').replace('-', '')}"])

if __name__ == "__main__": main()