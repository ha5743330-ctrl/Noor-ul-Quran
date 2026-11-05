# Feature checklist (build/redesign)

Scope: `index.html`, `generate.html` (+ Ayah Image UI/renderer), `admin-console.html`. No id/class/data-attr used by
app.js / picks.js / nav.js / admin.js was renamed or removed (verified by diffing ids against `main`: 0 removed on all 3 pages).
Hooks added only: `data-tab`, `data-pane`, `#mode-tiles [data-cm]`, `#tab-reel`, `#tab-image`, `#image-style`,
`#image-translation`, `#image-show-surah`, `#stat-videos|failures|render|views|ask|disk`.

## Generate (ids kept)
- [x] Single `#btn-pick` / Picks `#btn-picks` + `#picks-root` / Random `#btn-random` + `#input-random-count`
- [x] 4 modes: `#input-content-mode` (select kept, colored tiles sync it)
- [x] Reciter `#input-reciter`, Urdu voice `#input-urdu-voice`
- [x] Translation (English/Urdu, via mode + Ayah Image `#image-translation`)
- [x] Text animation `#input-text-animation`, text layout `#input-text-layout`, video style `#video-style-*`
- [x] Duration estimate `#duration-estimate-*`, suggestion chips `#range-suggestions`
- [x] Background Video/Photos `#input-background-type`, music `#input-music`
- [x] Quality / max duration, Generate `#generate-button`, daily limit `#daily-remaining`
- [x] Results: preview, download, ZIP, caption, history `#recent-videos-*`, Library link
- [x] Ayah Image: `#image-ayah`, `#image-theme`, `#image-suggest-button`, `#generate-image-button`, featured ayat list, photo-pool background
- [x] Ayah Image new: style Emerald Gold / Ivory / Night Sky, translation EN|UR, surah label toggle, ornament verse number, `BRAND_HANDLE` footer

## Other
- [x] Landing: hero, phone preview, Bismillah, tilted cards, how/modes/images, Demo (`/demo/*` real assets), limits, FAQ, CTA, footer `.contact-line`
- [x] Profile, Library, Search, Ask AI (untouched JS/layout; inherit tokens)
- [x] Theme toggle (light = Ivory Garden, dark = Midnight Emerald)
- [x] Admin: Users (search/filter/premium/block/sign-out/delete/load more), Clips, Photos, Music (uploads + progress),
      Picks (buckets/modes/add/import/export/recompute), Audit log, Diagnostics, Analytics (chart + table), Ask AI usage,
      `#state-warning` red banner, `#ad-status` Checking/Online/Offline pill, sticky horizontal tab bar

## Verification (Docker, RAQM)
- `pytest tests`: 267 passed.
- Every `tests/ui_*check.py` passes; `tests/ui_redesign_walk.py` (light+dark x 375/768/1280, 474 assertions) walks the boxes above:
  payloads for Single/Picks/Random/4 modes/reciter/animation/layout/Photos/music/quality, completed-job results (preview, counter, download, ZIP),
  Ayah Image style/translation/label payload, Library/Profile/Search/Ask AI pages, theme toggle, admin block/unblock, clip/photo/music upload + delete,
  picks rows, analytics chart, Ask AI usage, safe-mode banner, status pill. No console errors, no horizontal scroll.
- Admin/uploads run against a stubbed API (no real Supabase in CI).
