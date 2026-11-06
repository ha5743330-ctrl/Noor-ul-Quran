# Working rules (read first)
- Tests: NEVER rebuild a Docker image. Use the existing image `noor-redesign-test` (RAQM + pytest + Playwright): docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app noor-redesign-test pytest ...
- Rebuild only if requirements.txt or Dockerfile changed. App image `noor-ul-quran:raqm` is for running the app only; do not delete it.
- While iterating run only tests for the files you touched; run the full pytest + all ui_*check.py ONCE at the end. One container at a time.
- Branch per feature; merge to main only if all green; push origin main (no force); verify with git ls-remote; report the previous main hash.
- Never commit: .env, keys, design_ref/, AUDIT_V3.md, tests/screenshots/, tests/output/.
- Reports: short (3-6 lines). Prompts and answers are compact.
- Stack: FastAPI + plain HTML/JS, Pillow(RAQM)+FFmpeg, Supabase, Render (512 MB, MAX_VIDEO_SECONDS=35), Vercel.