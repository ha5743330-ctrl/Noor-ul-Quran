"""Fails on invalid/missing/duplicate featured refs. Run: python tools/validate_featured.py [--render]
--render also checks every ayah fits the image layout (needs Pillow+RAQM, i.e. the Docker image)."""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEMES = {"sabr/patience", "shukr/gratitude", "tawakkul/trust in Allah", "dua/supplication",
          "mercy/forgiveness", "guidance", "taqwa", "family", "knowledge", "hereafter", "tawhid",
          "character/akhlaq"}


def validate(render=False):
    featured = json.loads((ROOT / "data" / "featured_ayahs.json").read_text(encoding="utf-8"))
    quran = json.loads((ROOT / "data" / "quran_qpc_hafs.json").read_text(encoding="utf-8"))["surahs"]
    english = json.loads((ROOT / "data" / "quran.json").read_text(encoding="utf-8"))["surahs"]
    errors = []
    if "Needs human review" not in featured.get("status", ""):
        errors.append("status must keep 'Needs human review'")
    seen = set()
    for e in featured["ayahs"]:
        ref = e.get("ref", "")
        if not re.fullmatch(r"\d{1,3}:\d{1,3}", ref) or ref.split(":")[1] not in quran.get(ref.split(":")[0], {}):
            errors.append(f"invalid ref {ref!r}")
            continue
        if ref in seen:
            errors.append(f"duplicate {ref}")
        seen.add(ref)
        if e.get("theme") not in THEMES:
            errors.append(f"{ref}: bad theme {e.get('theme')!r}")
        if e.get("length") not in {"short", "medium", "long"}:
            errors.append(f"{ref}: bad length")
        if e.get("reviewed") is not False:
            errors.append(f"{ref}: reviewed must be false")
        surah, ayah = ref.split(":")
        if e.get("length") != "long" and len(english[surah]["english_ayahs"][ayah].split()) > 40:
            errors.append(f"{ref}: translation too long for image")
        if render and e.get("length") == "long":  # short ayahs always fit
            sys.path.insert(0, str(ROOT))
            import make, tempfile
            bg = next((ROOT / "backgrounds" / "images").glob("*.png"))
            try:
                make.render_ayah_image(make.verified_arabic_text(int(surah), int(ayah)),
                                       english[surah]["english_ayahs"][ayah], f"Surah {surah}:{ayah}",
                                       bg, Path(tempfile.gettempdir()) / "fit.png")
            except ValueError as exc:
                errors.append(f"{ref}: {exc}")
    return len(seen), errors


if __name__ == "__main__":
    count, errors = validate("--render" in sys.argv)
    print(f"{count} featured refs; {len(errors)} problems")
    for line in errors:
        print(" ", line)
    sys.exit(1 if errors else 0)
