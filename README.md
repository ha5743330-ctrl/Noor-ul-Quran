# Noor ul Quran: Quran Reel Maker

Pick ayat, choose a mode, and download a portrait reel (Arabic recitation plus Urdu or English text). Audio comes from EveryAyah, text is rendered with Pillow + RAQM, video is built with FFmpeg.

**Urdu quick start / اردو مختصر رہنمائی**
1. `.env.example` کو `.env` میں کاپی کریں اور Supabase کی چابیاں بھریں۔
2. Docker Desktop چلائیں، پھر `docker build -t noor .` اور نیچے دیا گیا `docker run` چلائیں۔
3. براؤزر میں `http://127.0.0.1:10000/` کھولیں، سائن اِن کریں اور ویڈیو بنائیں۔
4. لائیو سائٹ پر ویڈیو زیادہ سے زیادہ 35 سیکنڈ اور روزانہ 5 ہے؛ لمبی ویڈیو کے لیے لوکل ایپ استعمال کریں۔

## Features
- Four modes: **Urdu only (Urdu audio + text)**, **Arabic + Urdu (Arabic audio, Urdu audio)**, **Arabic + English text**, **Arabic + Urdu text (no Urdu audio)**.
- Arabic above, translation below, in the two Arabic-text modes. Optional text animation (`none`, `fade`, `fade_rise`): each phrase stays at least 1.2 s and renders take somewhat longer.
- Reciters and Urdu voices from `data/reciters.json`; per-element colour and font styles; optional background music (not in Arabic + English text mode).
- **Background photos:** admins upload photos in the console; one shared pool feeds Ayah Images and photo-background reels (Generate: *Background type*). Photos are cover-cropped to 1080x1920 and kept under 400 KB in the `noor-backgrounds` bucket under `images/`. The bucket must allow `video/mp4`, `image/jpeg` and `application/json` (the enable/disable list is `images/_disabled.json`).
- Text layout (one line / two lines / whole ayah), safe-zone layout, baked gradient, slow zoom, ayah-number ornament, cover image in the Library and audio fades on every render; an optional local-only **Pro** style (colour grade, music ducking, title card, `BRAND_HANDLE` watermark, 1080x1920) that the Render profile ignores.
- Ready-made Picks, random batches (Premium), ayah images, captions CSV, 3-day history, profile, admin console.
- Urdu text is Fateh Muhammad Jalandhry (AlQuran Cloud). If `data/urdu_usmani.json` exists (`{"93": {"1": "..."}}`), Mufti Taqi Usmani is used instead and must cover every ayah requested.

## Architecture
| Part | Where | Role |
|---|---|---|
| Static site (`public/`, `api/config.js`) | Vercel (`vercel.json`) | Landing, generate, library, admin pages; serves `/config.js` |
| API + renderer (`main.py`, `make.py`) | Render, Docker (`Dockerfile`, `render.yaml`) | FastAPI job queue, runs `make.py` with FFmpeg |
| Auth, database, storage (`schema.sql`) | Supabase | Accounts, history, private media bucket |

The browser calls the API at `API_BASE_URL` (default `/api`; set it to the Render URL when the site is on Vercel).

## Docker quick start
```bash
cp .env.example .env            # fill SUPABASE_* and VIDEO_URL_SECRET
docker build -t noor .
docker run --rm -p 10000:10000 --env-file .env -m 512m -e RENDER_MEMORY_PROFILE=low noor
```
Windows helper scripts: `start.ps1` (serves on http://127.0.0.1:8001/) and `rebuild.ps1`. See `SETUP-NEW-PC.md`.
Without Docker you need Python 3.11, FFmpeg/ffprobe on PATH and a Pillow build with RAQM (`pip install -r requirements.txt`).

## Environment variables
| Variable | Default | Purpose |
|---|---|---|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | none | Supabase project (public values) |
| `SUPABASE_SERVICE_ROLE_KEY` | none | Server-side key; never expose to the browser |
| `VIDEO_URL_SECRET` | random per start | Signs video links; set a fixed value or links break on restart |
| `CONTACT_EMAIL` | empty | Shown on Privacy, Terms and landing; hidden if unset |
| `API_BASE_URL` | `/api` | Where the browser finds the API |
| `FRONTEND_ORIGINS` | `http://127.0.0.1:8000,http://localhost:8000` | Allowed CORS origins when the site is on another domain |
| `DAILY_LIMIT` / `PREMIUM_DAILY_LIMIT` | 5 / same as `DAILY_LIMIT` | Reels per account per day |
| `MAX_VIDEO_SECONDS` | 35 | Server cap on reel length |
| `MAX_VIDEO_SECONDS_PHOTO` | same as `MAX_VIDEO_SECONDS` | Cap for photo-background reels (kept equal: a 60 s photo reel still peaked ~490 MiB, see below) |
| `MAX_ACTIVE_JOBS_PER_USER` | 1 | Concurrent jobs per user |
| `DEFAULT_QUALITY` | `fast` | `fast`, `balanced` or `high` |
| `CLEANUP_DAYS` | 3 | Age after which generated files are deleted |
| `RENDER_TIMEOUT_SECONDS` | 900 | Per-render timeout |
| `RENDER_MEMORY_PROFILE` | unset (`low` on Render) | `low` = smaller render for 512 MB hosts |
| `RENDER_MEMORY_LIMIT_MB` | 512 on Render, else off | Rejects requests whose estimate exceeds it |
| `SUPABASE_MEDIA_BUCKET`, `SUPABASE_BACKGROUNDS_BUCKET` | `noor-media`, `noor-backgrounds` | Storage buckets |
| `URDU_TRANSLATION_CREDIT` | source credit | Override the credit line under the Urdu text |

## Deploy
1. **Supabase:** create a project, put your admin user UUID into `schema.sql` (`<YOUR_ADMIN_USER_ID>`), run it in the SQL Editor (idempotent), and upload background clips to the backgrounds bucket.
2. **Render:** create a Web Service from this repo (Docker, `render.yaml`); set the secret env vars above; health check is `/api/health`.
3. **Vercel (optional):** import the repo, set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `API_BASE_URL` (Render URL + `/api`) and `CONTACT_EMAIL`.
4. Details and the memory guard: `DEPLOYING.md`, `DEPLOY-RENDER.md`, `docs/STAGING_CHECKLIST.md`.

## Limits
- Live site: 35 s per reel, 5 per day, one active job per user, files deleted after 3 days.
- Longer reels (up to 180 s) are for the local app, with limits raised in `.env` (`MAX_VIDEO_SECONDS`).
- Measured on a 512 MB / 0.5 CPU container (`RENDER_MEMORY_PROFILE=low`, real `/api/generate`, Arabic + Urdu text reel, none animation):

  | Background | Length | Render time | Peak (anon) |
  |---|---|---|---|
  | Video clips | 30 s | 172 s | 453 MiB |
  | Video clips | 60 s | 148 s* | 504 MiB |
  | Photos | 30 s | 26 s | 395 MiB |
  | Photos | 60 s | 35 s | 479 MiB |

  Photos render about 5x faster and use less memory, but 60 s is still above the 350 MiB bar for raising the cap. *The 60 s video run was capped by the container limit. `fade_rise` is downgraded to `fade` on the low profile. Without the low profile, 512 MB renders can be OOM-killed.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest                       # run inside the Docker image (needs RAQM and FFmpeg)
python tests/ui_generate_mobile_check.py   # also ui_check.py, ui_scroll_check.py, ui_legal_check.py, ...
```
The `ui_*check.py` scripts use Playwright (`pip install playwright && playwright install chromium`) with the API stubbed, and check 360/390/768/1280 px widths.

## Credits and licenses
- Arabic text: Quran.com data, shown in the KFGQPC Uthmanic Hafs font (`fonts/KFGQPC-Uthmanic-Hafs-LICENSE.txt`).
- Recitation audio: [EveryAyah.com](https://everyayah.com) (rights belong to the reciters).
- English: Pickthall; Urdu: Fateh Muhammad Jalandhry, both via AlQuran Cloud. Optional Urdu: Mufti Taqi Usmani (supplied by the site owner).
- Fonts: Amiri, Noto Naskh Arabic, Noto Nastaliq Urdu (SIL OFL, see `fonts/`).
- This repo has no LICENSE file yet; add one before accepting outside contributions. This tool does not replace the mushaf or a scholar.

## Troubleshooting
- **Reel killed or "memory" error on a small host:** set `RENDER_MEMORY_PROFILE=low`, choose `fast` quality and 30 s.
- **Arabic letters look disconnected:** Pillow was built without RAQM; use the Docker image.
- **Links stop working after a restart:** set a fixed `VIDEO_URL_SECRET`.
- **Contact line missing on Privacy/Terms:** set `CONTACT_EMAIL` (shown only when it is a valid address).
- **"Urdu text file has no text for ayah":** complete `data/urdu_usmani.json` or delete it to use Jalandhry.
- **Login or API calls fail from Vercel:** check `API_BASE_URL` and add the site origin to `FRONTEND_ORIGINS`.
- **Docker not running:** start Docker Desktop, then `.\start.ps1`.
