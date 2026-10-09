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
9. Sign in as the seeded admin and use Admin Access to upload MP4/MOV backgrounds, per-ayah Arabic/Urdu text overrides, and Arabic/Urdu audio. Generated videos and captions are stored in the private bucket and served with signed URLs.

## Hosting notes

Render Free services sleep after inactivity and use temporary local storage, so the first request can be slow while the service wakes. Supabase Free Storage currently includes 1 GB total and a 50 MB per-file limit; generated reels larger than 50 MB are rejected rather than silently lost. Storage fills over time, so old media may need deletion. Supabase Free projects can also pause after a week without activity.

Vercel's `config.js` rewrite serves the runtime Supabase and API configuration from `api/config.js`. Keep secrets in platform environment variables, not in source files. The publishable Supabase key is intended for the browser; the service-role key is not.