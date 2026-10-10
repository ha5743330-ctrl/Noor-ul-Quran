#!/usr/bin/env python3
"""Measure and cache Arabic and Urdu audio durations for every Quran ayah."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

import make


def measure_ayah(surah, ayah):
    for kind in ("arabic", "urdu"):
        if make.cached_ayah_duration(surah, ayah, kind) is None:
            seconds = make.measure_ayah_audio(surah, ayah, kind)
            if seconds is None:
                raise RuntimeError(f"{kind} audio unavailable for {surah}:{ayah}")
    return f"{surah}:{ayah}"


def main():
    quran_path = make.ROOT / "data" / "quran.json"
    quran = json.loads(quran_path.read_text(encoding="utf-8"))
    verses = [
        (surah, ayah)
        for surah in range(1, 115)
        for ayah in range(1, int(quran["surahs"][str(surah)]["n"]) + 1)
    ]
    existing = make.load_duration_cache()
    pending = [
        (surah, ayah)
        for surah, ayah in verses
        if not all(
            existing.get(f"{surah}:{ayah}", {}).get(kind)
            for kind in ("arabic", "urdu")
        )
    ]
    print(f"Audio duration cache: {len(verses) - len(pending)}/{len(verses)} complete; measuring {len(pending)}.")
    failures = []
    completed = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(measure_ayah, surah, ayah): (surah, ayah)
            for surah, ayah in pending
        }
        for future in as_completed(futures):
            completed += 1
            try:
                future.result()
            except Exception as exc:
                failures.append((futures[future], str(exc)))
            print(
                f"\rProgress: {completed}/{len(pending)}"
                f" ({len(failures)} failures)",
                end="",
                flush=True,
            )
    if pending:
        print()
    if failures:
        for (surah, ayah), error in failures:
            print(f"Failed {surah}:{ayah}: {error}")
        raise SystemExit(1)
    print("All 6236 ayah audio durations are cached.")


if __name__ == "__main__":
    main()
