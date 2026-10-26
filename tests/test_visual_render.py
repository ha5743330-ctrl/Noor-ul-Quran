import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat, features

import make


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_fatiha_ayah_1_2_visual_regression(monkeypatch, tmp_path):
    monkeypatch.setattr(make, "W", 1080)
    monkeypatch.setattr(make, "H", 1920)
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    surah = quran["surahs"]["1"]
    arabic_font = make.load_font("amiri_bold", 64)
    english_font = make.load_font("amiri_regular", 30)
    arabic_lines = []
    english_lines = []
    for ayah in ("1", "2"):
        arabic_lines.extend(
            make.wrap(
                surah["arabic"][ayah].lstrip("\ufeff"),
                arabic_font,
                940,
                language="ar",
            )
        )
        english_lines.extend(
            make.wrap_english(surah["english_ayahs"][ayah], english_font, 940)
        )

    base = tmp_path / "base.png"
    Image.new("RGBA", (1080, 1920), (18, 24, 38, 255)).save(base)
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    rendered = output_dir / "fatiha-ayahs-1-2.png"
    make.chunk_bilingual_png(
        rendered,
        arabic_lines,
        english_lines,
        arabic_font,
        english_font,
        base,
        {},
    )

    reference = Path(__file__).parent / "reference" / "fatiha-ayahs-1-2.png"
    if os.getenv("NOOR_UPDATE_VISUAL_REFERENCE") == "1":
        reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rendered, reference)
        return

    assert reference.is_file(), (
        f"Missing visual reference {reference}; generate it once with "
        "NOOR_UPDATE_VISUAL_REFERENCE=1 after verifying RAQM is enabled."
    )
    with Image.open(rendered) as actual, Image.open(reference) as expected:
        assert actual.size == expected.size == (1080, 1920)
        actual_crop = actual.crop((25, 600, 1055, 1500)).convert("RGB")
        expected_crop = expected.crop((25, 600, 1055, 1500)).convert("RGB")
        difference = ImageChops.difference(actual_crop, expected_crop)
        mean_difference = max(ImageStat.Stat(difference).mean)
    assert mean_difference <= 2.0, (
        f"Arabic/English frame differs from reference (mean channel difference "
        f"{mean_difference:.3f}; limit 2.0). Inspect {rendered}."
    )
