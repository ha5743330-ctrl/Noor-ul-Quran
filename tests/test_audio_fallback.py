import pytest
import make
from test_v3_core import Resp, audio  # noqa: F401


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "FALLBACK_DIR", tmp_path / "fb")
    monkeypatch.delenv("AUDIO_PROVIDERS", raising=False)
    make._fallback_audio.clear(); make.fallback_used.clear()


def calls_to(monkeypatch, fn):
    seen = []
    def get(url, **k):
        seen.append(url)
        return fn(url)
    monkeypatch.setattr(make.requests, "get", get)
    return seen


def test_ayah_counts_total():
    assert sum(make.AYAH_COUNTS) == 6236 and len(make.AYAH_COUNTS) == 114


@pytest.mark.parametrize("code", [503, 404])
def test_order_everyayah_then_islamic(audio, monkeypatch, code):
    seen = calls_to(monkeypatch, lambda u: Resp(200) if "islamic" in u else Resp(code))
    p = make.fetch_ayah("arabic", 2, 1)
    assert p is not None and p.exists()
    first_islamic = next(i for i, u in enumerate(seen) if "islamic" in u)
    assert all("everyayah" in u for u in seen[:first_islamic]) and first_islamic >= 1
    assert seen[first_islamic].endswith("ar.alafasy/8.mp3")  # 2:1 follows the 7 ayahs of Al-Fatiha
    assert make.fallback_used == {"islamic"}


def test_primary_success_never_calls_fallback(audio, monkeypatch):
    seen = calls_to(monkeypatch, lambda u: Resp(200))
    assert make.fetch_ayah("arabic", 93, 3).exists()
    assert len(seen) == 1 and "everyayah" in seen[0] and not make.fallback_used


def test_no_fallback_keeps_original_error(audio, monkeypatch):
    seen = calls_to(monkeypatch, lambda u: Resp(503))
    with pytest.raises(RuntimeError, match="EveryAyah download failed for 93:3"):
        make.fetch_ayah("urdu", 93, 3)  # no Urdu edition -> islamic never called
    assert not any("islamic" in u for u in seen)


def test_both_fail_raises_original(audio, monkeypatch):
    calls_to(monkeypatch, lambda u: Resp(503))
    with pytest.raises(RuntimeError, match="EveryAyah download failed"):
        make.fetch_ayah("arabic", 93, 3)


def test_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AUDIO_PROVIDERS", "bogus")
    assert make.audio_fallbacks() == ["islamic"]
    monkeypatch.setenv("AUDIO_PROVIDERS", "everyayah")
    assert make.audio_fallbacks() == []
