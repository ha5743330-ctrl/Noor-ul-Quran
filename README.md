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

---

## 🛠️ Step 1: Initial Setup (Sirf Ek Baar)

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

### 4. Enable Urdu Nastaliq on Windows

Pillow needs RAQM/FriBiDi to shape Nastaliq text. Check it with:
```powershell
.\venv\Scripts\python -c "from PIL import features; print(features.check('raqm'))"
```

If it prints `False`, install MSYS2, then run `pacman -S mingw-w64-x86_64-fribidi` in its MinGW64 terminal. Copy `C:\msys64\mingw64\bin\libfribidi-0.dll` to the project `venv\Scripts\` folder and restart the terminal. The generator stops with an explanatory error rather than producing unreadable Urdu if RAQM is unavailable.

### 5. Download Quran text

Run this once, or whenever `data/quran.json` is missing:
```powershell
.\venv\Scripts\python fetch_assets.py
```

This downloads Uthmani Arabic text and the Urdu translation into `data/quran.json`.

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

You can check the reel plan without rendering with:
```powershell
.\venv\Scripts\python make.py --pick 112:1-4 --dry
```

### Deployment

Deployment setup is intentionally left for later; this README covers local development only.