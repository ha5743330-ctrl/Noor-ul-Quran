# Deployment

The frontend is a static Vercel site. The Python/FFmpeg renderer runs as a Docker web service on Render Free. Supabase provides Auth, database roles, and private media storage so videos/clips survive Render's ephemeral filesystem.

## Required setup

1. Push the project files to the connected GitHub repository. `.env`, `venv/`, generated output, audio, backgrounds, and cache are excluded from deployment.
2. Import the repository into Vercel as an **Other** framework project with the repository root as the output directory. No frontend build command is needed.
3. Set these Vercel environment variables for Production and Preview:
   - `SUPABASE_URL`: the Supabase project URL.
   - `SUPABASE_ANON_KEY`: the Supabase publishable key. Never use the service-role key.
   - `API_BASE_URL`: set this after Render is deployed, using `https://YOUR_RENDER_SERVICE.onrender.com/api`.
4. Re-run the updated `schema.sql` in the Supabase SQL Editor. It is idempotent and creates/updates the private `noor-media` Storage bucket for generated media, background clips, admin verse text overrides, and Arabic/Urdu audio.
5. Create a Render Blueprint from the same repository and select `render.yaml`. It builds the `Dockerfile` and installs FFmpeg. Render Free's local filesystem is temporary; the app copies media to/from Supabase Storage.
6. Render prompts for `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and `FRONTEND_ORIGINS`. Set `FRONTEND_ORIGINS` to the Vercel site's exact origin, such as `https://your-project.vercel.app`.
   - Enter the service-role/secret key only in Render's server environment. Never use it in Vercel or browser code, and never send it in chat.
7. Copy the Render service URL into Vercel's `API_BASE_URL`, then redeploy the Vercel project.
8. In Supabase Authentication URL Configuration, add the Vercel origin and `https://your-project.vercel.app/**` to the allowed redirect URLs. Keep the local `http://127.0.0.1:8000/**` redirect for local development if needed.
9. Sign in as the seeded admin and use Admin Access to upload MP4/MOV backgrounds, background music, per-ayah Arabic/Urdu text overrides, and Arabic/Urdu audio. These media assets are saved in the private bucket and downloaded to Render's temporary disk when needed. Generated videos and captions are stored in the private bucket and served with signed URLs.

## RAQM and Arabic shaping

Pillow must report RAQM enabled before any Arabic or Urdu text is rendered. The startup log
reports the capability, and `/api/generate` returns HTTP 503 with a clear message instead of
rendering broken script when it is unavailable. Full, Urdu-only, and Arabic-only modes are
blocked because each renders Arabic-derived script.

### Windows development

The project's pip-installed Pillow 12.3.0 Windows wheel reported `False` for RAQM even with
`libfribidi-0.dll` in `venv\Scripts`. A DLL cannot add RAQM support to a Pillow build compiled
without it. Use the conda-forge build, which supplies Pillow and libraqm's FriBiDi/HarfBuzz
dependencies together:

```powershell
winget install --id CondaForge.Miniforge3 --exact --scope user
# Open a new PowerShell window.
conda create -y -n noor-ul-quran-raqm -c conda-forge python=3.14 pillow libraqm fribidi harfbuzz freetype
conda activate noor-ul-quran-raqm
python -m pip install -r requirements-dev.txt
python -c "from PIL import features; print(features.check('raqm')); assert features.check('raqm')"
python -m uvicorn main:app --reload
```

Run the app and tests from this activated environment. Do not use the old `venv` for Arabic
rendering unless its RAQM check also prints `True`.

### Docker production image

The Dockerfile installs `libraqm-dev`, `libfribidi-dev`, `libharfbuzz-dev`, FreeType, and
Noto fonts, builds Pillow from source against those libraries, and asserts RAQM is enabled
during the image build. Verify locally from PowerShell:

```powershell
docker build -t noor-ul-quran:raqm .
docker run --rm --entrypoint python noor-ul-quran:raqm -c "from PIL import features; print(features.check('raqm')); assert features.check('raqm')"
```

### Translation edition and usage

Arabic-only English text uses **The Meaning of the Glorious Koran**, translated by Marmaduke
William Pickthall, from AlQuran Cloud API edition **`en.pickthall`**
(`https://api.alquran.cloud/v1/quran/en.pickthall`). The API's edition metadata names
Pickthall, but does not provide an explicit license for its API payload. Project Gutenberg
lists its 2005 compilation containing the Pickthall translation as public domain in the USA
([eBook 16955](https://www.gutenberg.org/ebooks/16955)). The generator UI attributes the
translator. Confirm local copyright law and the API's current service terms before
redistributing the data outside the USA.

### Supabase production smoke test

Required variables are `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and (for private storage)
`SUPABASE_SERVICE_ROLE_KEY` on the Render backend, plus `FRONTEND_ORIGINS` on Render and
`API_BASE_URL` on Vercel. `SUPABASE_URL` and the publishable `SUPABASE_ANON_KEY` must also be
set for the Vercel frontend. Never put the service-role key in Vercel or browser code. The
local names and placeholders are listed in `.env.example`; verify only that values are set,
never print them.

1. Deploy the Docker image and confirm Render's `/api/health` returns HTTP 200. As an admin,
   check `/api/admin/diagnostics` reports `"raqm": true`.
2. Open the production Vercel site in a private browser window, sign in through Supabase,
   and confirm the browser returns to `/generate.html` with the signed-in user shown.
3. Generate a short Al-Ikhlas reel in each content mode, then play and download each reel.
   Confirm Arabic and Urdu script are shaped correctly; Arabic-only must have Arabic recitation,
   English translation attribution, and no music.
4. Refresh Recent Videos and verify the signed-in user's reels remain available. If Storage
   is configured, verify uploaded/generated objects appear in the private `noor-media` bucket.
5. Sign out and verify `/api/history` and `/api/generate` return HTTP 401.

An actual production sign-in/generation run requires a production browser session and valid
Supabase user credentials; do not place credentials in logs or chat.

## Moving local assets to hosted storage

- Upload required background clips and music from **Admin Access** after signing in to the deployed site. Do not commit media files to Git; `.gitignore` intentionally excludes them. After a Render restart, Supabase Storage is the source and the backend downloads those assets again.
- The local background videos currently total more than Supabase Free's 1 GB storage allowance. Select only the clips you intend to use and compress any clip over the 50 MB per-file limit before uploading; do not try to upload the complete local folder unchanged.
- The `audio/arabic` and `audio/urdu` folders are downloaded audio cache. The renderer fetches missing verse audio from EveryAyah as needed, so these cache files do not need to be deployed. Admin-uploaded verse audio is saved to Supabase Storage.
- `data/quran.json` (Uthmani Arabic, Urdu Jalandhry, and Pickthall English translation) is fetched while the Docker image is built. Admin-edited verse text and custom audio are stored in Supabase. The required renderer fonts are tracked in Git and included in the image.
- Verify a background and music track appear in the admin lists, then generate a reel using each before relying on the deployment.

## Hosting notes

Render Free services sleep after inactivity and use temporary local storage, so the first request can be slow while the service wakes. Supabase Free Storage currently includes 1 GB total and a 50 MB per-file limit; generated reels larger than 50 MB are rejected rather than silently lost. Storage fills over time, so old media may need deletion. Supabase Free projects can also pause after a week without activity.

Vercel's `config.js` rewrite serves the runtime Supabase and API configuration from `api/config.js`. Keep secrets in platform environment variables, not in source files. The publishable Supabase key is intended for the browser; the service-role key is not.