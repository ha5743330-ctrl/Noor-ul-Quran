import json
from pathlib import Path

import pytest

import search

QURAN = Path(__file__).resolve().parents[1] / "data" / "quran.json"
pytestmark = pytest.mark.skipif(not QURAN.exists(), reason="quran.json is built by fetch_assets.py")


@pytest.fixture(scope="module")
def idx():
    return search.build_index(json.loads(QURAN.read_text(encoding="utf-8")))


def refs(idx, q):
    return [r["ref"] for r in search.search(idx, q, 200)[1]]


def test_index_covers_whole_quran(idx):
    assert len(idx) == 6236


def test_arabic_without_diacritics(idx):
    assert "112:1" in refs(idx, "قل هو الله احد")
    assert "2:255" in refs(idx, "الحي القيوم")


def test_english_and_urdu(idx):
    assert "112:1" in refs(idx, "He is Allah, the One")
    assert any(r.startswith("2:") for r in refs(idx, "خدا"))


def test_reference_jump_and_links(idx):
    total, res = search.search(idx, "2:255")
    assert total == 1 and res[0]["tafsir_url"] == "https://quran.com/2/255/tafsirs"
    assert res[0]["generate_url"] == "/generate.html?pick=2:255-255"


def test_short_or_empty_query_returns_nothing(idx):
    assert search.search(idx, "a") == (0, []) and search.search(idx, "  ") == (0, [])
