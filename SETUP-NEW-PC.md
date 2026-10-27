# Set up Noor ul Quran on another Windows PC

## 1. Install Docker Desktop

Install Docker Desktop for Windows, start it, and wait until the engine is running.
Docker Desktop supplies Docker; no separate Python or FFmpeg installation is needed.

## 2. Get the project

Clone the repository and check out the branch containing the setup scripts:

```powershell
git clone https://github.com/ha5743330-ctrl/Noor-ul-Quran.git
Set-Location Noor-ul-Quran
git checkout hardening
```

## 3. Copy local configuration and media

Copy `.env` securely from the existing PC into the project root. It contains secrets:
never commit it, put it in a public cloud folder, or send it in chat. Configure the
Supabase values on the new PC if you do not transfer the existing file.

Copy the root-level `.mp4`/`.mov` clips in `backgrounds/` from the existing PC if the
project does not already have a background clip available in Supabase Storage. At least one
root-level clip is needed to render. ZIP archives and clips under `backgrounds/New folder/`
are not needed by the renderer.
Copy `audio/` only to reuse the existing downloaded audio cache; missing verse audio can
be fetched when online. `cache/` and `output/` are optional and can be copied to preserve
random-selection state, generated reels, and local history. The Docker build fetches
`data/quran.json`; it does not need to be copied.

On this PC, the current local data sizes are approximately:

| Path | Size | Needed to run? |
|---|---:|---|
| `.env` | 462 B | Yes, for configured Supabase/auth |
| Root-level background `.mp4`/`.mov` clips (15 files) | 809,317,682 B (772 MiB) | Yes, unless already available from Storage |
| Nested duplicate `.mp4` clips | 695,849,983 B (663 MiB) | No |
| `audio/` | 343,022,786 B (327 MiB) | No; audio is fetched on demand |
| `backgrounds/music/` | 20,719,527 B (19.8 MiB) | No; optional background music |
| `cache/` | 62,264,963 B (59.4 MiB) | No; recreated as needed |
| `output/` | 68,933,855 B (65.8 MiB) | No; recreated; copy to preserve existing reels/history |
| `data/quran.json` | 3,963,209 B (3.78 MiB) | No; fetched during image build |
| Background ZIP archive | 691,832,880 B (660 MiB) | No |

Copying `.env`, audio, all background clips/music, cache, output, and Quran JSON as listed
uses about 2,004,072,467 bytes (1.87 GiB), excluding the unnecessary ZIP archive. A
minimal offline-data copy is `.env` plus the 15 root-level video clips: 809,318,144 bytes
(about 772 MiB). For less transfer, one root-level clip is sufficient; audio, music, cache,
output, nested duplicates, and Quran JSON do not need to be copied.

## 4. Build and start

From the project root, run:

```powershell
.\rebuild.ps1
```

The script builds the RAQM-enabled Docker image, creates the `noor-raqm-app` container,
and verifies `/api/health` returns HTTP 200. Open:

<http://127.0.0.1:8001/>

Later, start the existing container and open the site with:

```powershell
.\start.ps1
```
