# Deploy: Render (API) + Vercel (frontend) + Supabase (clips)

## Render
- New **Blueprint** from this repo (`render.yaml`), or Web Service with runtime **Docker**, Dockerfile `./Dockerfile`, context `.`, branch `main`.
- Health check path: `/api/health`. Port: Render's `$PORT` (the image reads it). 1 uvicorn worker.
- `RENDER_MEMORY_PROFILE=low` (480x854 render, one loudnorm pass) fits the 512 MB plan.
- `data/quran.json` is built during `docker build` (`fetch_assets.py`, needs internet); it is not in git.
- Durations cache lives in `cache/ayah_durations.json` (gitignored, rebuilt at runtime).

## Env vars
Render: `RENDER_MEMORY_PROFILE` (low), `OUTPUT_DIR` (/tmp/noor/output), `CACHE_DIR` (/tmp/noor/cache),
`SUPABASE_BACKGROUNDS_BUCKET` (backgrounds), `SUPABASE_MEDIA_BUCKET` (noor-media), `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `FRONTEND_ORIGINS` (your Vercel origin), `VIDEO_URL_SECRET` (auto-generated).
Optional: `RENDER_TIMEOUT_SECONDS`. Missing Supabase vars do not crash startup; features needing them return 503.

Vercel: `API_BASE_URL` = `https://<service>.onrender.com/api` (served to the browser by `api/config.js`).

## Background clips (Supabase)
1. Supabase > Storage > create bucket named by `SUPABASE_BACKGROUNDS_BUCKET` (default `backgrounds`).
2. Upload `.mp4`/`.mov` clips at the bucket root (each <= 50 MB; 9:16, 1080x1920 is fine).
3. On the first render the API lists the bucket and downloads missing clips into `cache/backgrounds/`.
4. No clips (or bucket unreachable): renders fall back to a looped still from `backgrounds/images/` (committed).
Music (`backgrounds/music/`) is committed to git.

## Verify locally like Render
```
docker build -t noor .
docker run --memory=512m --cpus=0.5 -e RENDER_MEMORY_PROFILE=low -p 10000:10000 noor
curl localhost:10000/api/health
```
Measured (empty backgrounds/): Al-Ikhlas balanced video peak ~252 MiB, ~48 s; one Ayah image peak ~93 MiB, ~8 s.
