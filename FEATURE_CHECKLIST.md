# Feature checklist (build/redesign)

Scope: `index.html`, `generate.html` (+ Ayah Image UI/renderer), `admin-console.html`. No id/class/data-attr used by
app.js / picks.js / nav.js / admin.js was renamed or removed (verified by diffing ids against `main`: 0 removed on all 3 pages).
Hooks added only: `data-tab`, `data-pane`, `#mode-tiles [data-cm]`, `#tab-reel`, `#tab-image`, `#image-style`,
`#image-translation`, `#image-show-surah`, `#stat-videos|failures|render|views|ask|disk`.

## Generate (ids kept)
- [ ] Single `#btn-pick` / Picks `#btn-picks` + `#picks-root` / Random `#btn-random` + `#input-random-count`
- [ ] 4 modes: `#input-content-mode` (select kept, colored tiles sync it)
- [ ] Reciter `#input-reciter`, Urdu voice `#input-urdu-voice`
- [ ] Translation (English/Urdu, via mode + Ayah Image `#image-translation`)
- [ ] Text animation `#input-text-animation`, text layout `#input-text-layout`, video style `#video-style-*`
- [ ] Duration estimate `#duration-estimate-*`, suggestion chips `#range-suggestions`
- [ ] Background Video/Photos `#input-background-type`, music `#input-music`
- [ ] Quality / max duration, Generate `#generate-button`, daily limit `#daily-remaining`
- [ ] Results: preview, download, ZIP, caption, history `#recent-videos-*`, Library link
- [ ] Ayah Image: `#image-ayah`, `#image-theme`, `#image-suggest-button`, `#generate-image-button`, featured ayat list, photo-pool background
- [ ] Ayah Image new: style Emerald Gold / Ivory / Night Sky, translation EN|UR, surah label toggle, ornament verse number, `BRAND_HANDLE` footer

## Other
- [ ] Landing: hero, phone preview, Bismillah, tilted cards, how/modes/images, Demo (`/demo/*` real assets), limits, FAQ, CTA, footer `.contact-line`
- [ ] Profile, Library, Search, Ask AI (untouched JS/layout; inherit tokens)
- [ ] Theme toggle (light = Ivory Garden, dark = Midnight Emerald)
- [ ] Admin: Users (search/filter/premium/block/sign-out/delete/load more), Clips, Photos, Music (uploads + progress),
      Picks (buckets/modes/add/import/export/recompute), Audit log, Diagnostics, Analytics (chart + table), Ask AI usage,
      `#state-warning` red banner, `#ad-status` Checking/Online/Offline pill, sticky horizontal tab bar
