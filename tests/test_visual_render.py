import json
import hashlib
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat, features

import make


def test_qpc_hafs_data_matches_checksummed_source():
    data_dir = make.ROOT / "data"
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    source_path = data_dir / "quran_qpc_hafs.json"
    expected_hash = (data_dir / "quran_qpc_hafs.sha256").read_text(
        encoding="ascii"
    ).split()[0]
    actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = json.loads(source_path.read_text(encoding="utf-8"))

    assert actual_hash == expected_hash
    assert source["edition"] == "text_qpc_hafs"
    assert sum(len(ayahs) for ayahs in source["surahs"].values()) == 6236
    assert {
        number: surah["arabic"] for number, surah in quran["surahs"].items()
    } == source["surahs"]
    assert "\u06ed" not in quran["surahs"]["41"]["arabic"]["34"]


def test_ayah_text_without_bismillah_for_surah_23():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    text = quran["surahs"]["23"]["arabic"]["1"]
    prefix_size = len(make.QPC_BISMILLAH_TEXT.split()) - 1
    normalize = lambda value: [
        make.strip_harakat_surah(word).replace("ٱ", "ا")
        for word in value.split()[:prefix_size]
    ]

    assert normalize(make.ayah_text_without_bismillah(text, 23)) != normalize(
        make.QPC_BISMILLAH_TEXT
    )


def test_ayah_text_without_bismillah_for_surah_112():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    text = quran["surahs"]["112"]["arabic"]["1"]
    prefix_size = len(make.QPC_BISMILLAH_TEXT.split()) - 1
    normalize = lambda value: [
        make.strip_harakat_surah(word).replace("ٱ", "ا")
        for word in value.split()[:prefix_size]
    ]

    assert normalize(make.ayah_text_without_bismillah(text, 112)) != normalize(
        make.QPC_BISMILLAH_TEXT
    )


def test_ayah_text_keeps_bismillah_for_surah_1():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    text = quran["surahs"]["1"]["arabic"]["1"]

    assert make.ayah_text_without_bismillah(text, 1) == text.lstrip("\ufeff")


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_fatiha_ayah_1_2_visual_regression(monkeypatch, tmp_path):
    monkeypatch.setattr(make, "W", 1080)
    monkeypatch.setattr(make, "H", 1920)
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    surah = quran["surahs"]["1"]
    arabic_font = make.load_font(make.QURAN_ARABIC_FONT, 64)
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


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_featured_ayah_image_visual_render(tmp_path):
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    ayah = quran["surahs"]["112"]
    background = tmp_path / "background.png"
    rendered = Path(__file__).parent / "output" / "featured-ayah-visual.png"
    rendered.parent.mkdir(exist_ok=True)
    Image.new("RGB", (800, 1200), (33, 60, 78)).save(background)

    make.render_ayah_image(
        ayah["arabic"]["1"],
        ayah["english_ayahs"]["1"],
        "Surah Al-Ikhlaas 112:1",
        background,
        rendered,
    )

    with Image.open(rendered) as image:
        assert image.size == (1080, 1350)
        assert image.format == "PNG"
        assert len(image.convert("RGB").getcolors(maxcolors=2_000_000) or []) > 100


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_quranic_arabic_line_bounds_do_not_overlap():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    text = quran["surahs"]["2"]["arabic"]["255"]
    font = make.load_font(make.QURAN_ARABIC_FONT, 48)
    lines = make.wrap(text, font, 800, language="ar")
    draw = ImageDraw.Draw(Image.new("RGBA", (1000, 2000)))
    positions, _, _ = make.quran_arabic_line_layout(lines, font, stroke=2)
    bounds = [
        draw.textbbox(
            (500, positions[index]),
            line,
            font=font,
            anchor="ma",
            stroke_width=2,
            direction="rtl",
            language="ar",
        )
        for index, line in enumerate(lines)
    ]

    assert len(bounds) > 1
    assert all(previous[3] <= current[1] for previous, current in zip(bounds, bounds[1:]))
    assert all(
        current - previous >= int(round(font.size * make.QURAN_ARABIC_LINE_SPACING))
        for previous, current in zip(positions, positions[1:])
    )


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_surah_title_glyphs_exist_in_quran_font():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    font = make.load_font("amiri_bold", 48)
    missing_glyph = bytes(font.getmask("\U0010ffff"))
    title_characters = {
        character
        for number in range(1, 115)
        for character in make.normalize_surah_title(
            "سورۃ " + make.strip_harakat_surah(quran["surahs"][str(number)]["name"])
        )
        if not character.isspace()
    }
    title_characters.update(character for character in "آيات ١ تا ٤" if not character.isspace())

    missing = [
        character for character in sorted(title_characters)
        if bytes(font.getmask(character)) == missing_glyph
    ]

    assert not missing, f"Surah title font has missing glyphs: {missing!r}"


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
def test_waqf_mark_stays_attached_and_arabic_lines_are_balanced():
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    font = make.load_font(make.QURAN_ARABIC_FONT, 48)
    text = quran["surahs"]["17"]["arabic"]["36"]
    lines = make.wrap(text, font, 520, language="ar")

    waqf_word = next(word for word in text.split() if "\u06da" in word)
    assert waqf_word.endswith("\u06da")
    assert any(waqf_word in line for line in lines)
    assert not any(line.strip() == "ۚ" for line in lines)

    balanced_lines = make.wrap(
        "وَإِن يَمْسَسْكَ ٱللَّهُ بِضُرٍّ فَلَا كَاشِفَ لَهُ",
        font,
        500,
        language="ar",
    )
    assert len(balanced_lines[-1].split()) > 1


@pytest.mark.skipif(
    not features.check("raqm"),
    reason="Pillow features.check('raqm') is False; Arabic shaping cannot be visually validated.",
)
@pytest.mark.parametrize("is_arabic", [False, True])
def test_video_text_bounding_boxes_stay_inside_dynamic_card(
    is_arabic, monkeypatch, tmp_path
):
    monkeypatch.setattr(make, "W", 1080)
    monkeypatch.setattr(make, "H", 1920)
    quran = json.loads((make.ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
    surah = quran["surahs"]["110"]
    text = " ".join(
        (surah["arabic"] if is_arabic else surah["ayahs"])[str(ayah)]
        for ayah in range(1, 4)
    )
    font = make.load_font(
        make.QURAN_ARABIC_FONT if is_arabic else "nastaliq_regular",
        54,
    )
    language = "ar" if is_arabic else "ur"
    lines = make.wrap(text, font, 750, language=language)
    base = tmp_path / "base.png"
    Image.new("RGBA", (1080, 1920), (20, 30, 40, 255)).save(base)

    layout = make.chunk_png(
        tmp_path / "text.png",
        lines,
        font,
        make.QURAN_ARABIC_LINE_SPACING if is_arabic else 1.8,
        base,
        is_arabic=is_arabic,
    )

    card_top, card_bottom = layout["card_bounds"]
    assert len(layout["text_bounds"]) == len(lines)
    assert all(
        card_top <= top < bottom <= card_bottom
        for top, bottom in layout["text_bounds"]
    )
