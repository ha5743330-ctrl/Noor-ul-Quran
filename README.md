 # 🌙 Noor ul Quran – Auto Reel Maker

Automated tool jo Quran Majeed ki Ayaat aur Urdu tarjuma ki high-quality Instagram Reels / TikTok / YouTube Shorts videos banata hai.

Har ayat ki audio (**Arabic Tilawat + Urdu Tarjuma**) EveryAyah.com se khud-ba-khud download hoti hai. Alag se audio download ya cut karne ki zaroorat nahi hai.

---

## 📋 Features
- **Auto Audio Fetching:** Arabic aur Urdu audio automatic download hoti hain.
- **Auto Text Sync:** Tilawat ke waqt Arabic text (Zair/Zabar ke sath) aur Tarjuma ke waqt Urdu text screen par show hota hai.
- **Custom Durations:** Standard (20-60 sec) aur Long Waqia/Story videos (up to 90 sec ya us se zyada).
- **No-Repeat System:** Random mode mein koi bhi ayat repeat nahi hoti (`cache/used.json` track rakhta hai).
- **Auto Captions:** Har video ke sath CSV file mein tayar caption aur hashtags milte hain.
- **Ayah Images:** Generate a 1080×1350 still with Arabic, Pickthall's English translation, and a random image background; optionally enter a curated `Surah:Ayah`.

---

## 🛠️ Step 1: Initial Setup (Sirf Ek Baar)

### Local Docker app

After Docker Desktop is running and `.env` is configured, use these PowerShell scripts
from the project root:

```powershell
.\start.ps1
.\rebuild.ps1
```

`start.ps1` starts the existing `noor-raqm-app` container or creates it, then opens
`http://127.0.0.1:8001/`. `rebuild.ps1` rebuilds the RAQM-enabled image, replaces the
container, and verifies its API health endpoint.
For setup on another Windows PC, follow [SETUP-NEW-PC.md](SETUP-NEW-PC.md).

### 1. Requirements Install Karein
Python aur FFmpeg install karne ke baad terminal mein chalaein:
```bash
pip install -r requirements.txt
```

### 2. Install FFmpeg

FFmpeg and ffprobe must be available on PATH.

Windows (winget):
```powershell
winget install Gyan.Dev.FFmpeg
```

Ubuntu/Debian:
```bash
sudo apt update
sudo apt install ffmpeg
```

Open a new terminal after installation, then verify with `ffmpeg -version` and `ffprobe -version`.

### 3. Create the local environment

From the project root on Windows:
```powershell
py -3 -m venv venv
.\venv\Scripts\python -m pip install --upgrade pip
.\venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `SUPABASE_URL` and `SUPABASE_ANON_KEY` from your Supabase project. Keep `SUPABASE_SERVICE_ROLE_KEY` blank for local-only media/output, or set a matching server-only key when using private Supabase Storage. Never put a service key in frontend code or commit `.env`.

### 4. Enable Arabic shaping on Windows

The pip-installed Pillow 12.3.0 wheel in this workspace reports `RAQM=False`; copying a
FriBiDi DLL beside Python cannot enable support if Pillow was built without RAQM. Use
Miniforge/conda-forge to get a Pillow build linked with libraqm and its dependencies:

```powershell
winget install --id CondaForge.Miniforge3 --exact --scope user
# Open a new PowerShell window after installation.
conda create -y -n noor-ul-quran-raqm -c conda-forge python=3.14 pillow libraqm fribidi harfbuzz freetype
conda activate noor-ul-quran-raqm
python -m pip install -r requirements-dev.txt
python -c "from PIL import features; print(features.check('raqm')); assert features.check('raqm')"
```

Run the app and renderer from this activated conda environment, not `venv\Scripts\python.exe`.
All three modes contain Arabic-derived scripts and are blocked by the API when Pillow reports
RAQM unavailable. The startup log and API response explain how to resolve it; broken shaping
is never rendered.

### 5. Download Quran text

Run this once, whenever `data/quran.json` is missing, or to add the English translation to an existing data file:
```powershell
.\venv\Scripts\python fetch_assets.py
```

This downloads Uthmani Arabic text, the Urdu translation, and Marmaduke William Pickthall's
English translation into `data/quran.json`. The Arabic-only option shows the translator
attribution in the generator UI and generated ayah images. The image feature's curated
references in `data/featured_ayahs.json` are explicitly marked **Needs human review**.

The English text comes from the AlQuran Cloud API endpoint
`https://api.alquran.cloud/v1/quran/en.pickthall` (edition ID `en.pickthall`). AlQuran Cloud's
edition metadata identifies the translator as Mohammed Marmaduke William Pickthall but does
not state a text license. Project Gutenberg's record for its 2005 compilation containing
Pickthall's translation marks that compilation “Public domain in the USA”
([eBook 16955](https://www.gutenberg.org/ebooks/16955)). The translation was published in
1930; check applicable local copyright rules and AlQuran Cloud's current service terms before
redistributing the API data outside the United States.

In the video generator, choose **Arabic recitation + Arabic text with English translation** for Arabic-only recitation videos. The Arabic ayah is shown above its English translation, with no Urdu audio or background music.

### 6. Add background clips

Place portrait `.mp4` or `.mov` clips in `backgrounds/`. Use clips with enough resolution for a 9:16 reel. Background video files are local assets and are not included in Git.

### 7. Configure Supabase access

In the Supabase Dashboard, open **SQL Editor**. Before running `schema.sql`, replace `<YOUR_ADMIN_USER_ID>` with your user UUID from **Authentication > Users**. Run the complete SQL once. Add the project URL and anon/public key to local `.env`, then restart the server. Sign in at `/auth.html`; the configured user receives admin access.

### 8. Start the local website

From the project root:
```powershell
.\venv\Scripts\python -m uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop the server with `Ctrl+C`.

The root URL is the public landing page. Sign in at `/auth.html`, then open the generator with **Video banayein**, `/generate.html`, or the `/generate` shortcut. If you open the generator while signed out, it sends you to sign in and returns you to the generator afterward.

Free accounts can create single-pick reels up to 60 seconds. Custom picks, random batches, and durations above 60 seconds require Premium access granted by an administrator.

You can check the reel plan without rendering with:
```powershell
.\venv\Scripts\python make.py --pick 112:1-4 --dry
```

### Deployment

The app is deployed on Render's free Docker plan. Its `/tmp` disk is ephemeral: videos and the local Recent Videos history can disappear when the service restarts or redeploys. Download videos you need to keep outside the app.

Generated videos remain in Recent Videos until you manually delete them; the app does not limit the list or automatically remove MP4 files. When configured, Supabase Storage keeps uploaded media objects beyond a Render restart. The Recent Videos history index is currently stored locally on Render, however, so persistent history metadata requires storing that index in Supabase as well; it is not currently persisted there.

See [DEPLOYING.md](DEPLOYING.md) for the Docker RAQM build check, required Supabase variables,
and signed-in production smoke-test steps.

## Credits & licenses

Quranic Arabic text in images and videos uses Amiri Quran (Google Fonts, Amiri Project
authors), bundled under the SIL Open Font License 1.1; see `fonts/AmiriQuran-OFL.txt`.
Urdu typography remains unchanged.

Record the source and applicable license for each media category before release:

- EveryAyah audio — Source: ____________________ · License: ____________________
- Background clips — Source: ____________________ · License: ____________________
- Nasheed music — Source: ____________________ · License: ____________________

## Release flow

Create a branch, open a pull request, wait for the CI checks to pass, then merge the pull request.