import json
from pathlib import Path

import pytest
import requests

import ask
import main
import search
from test_hardening import client, FREE_USER  # noqa: F401

QURAN = Path(__file__).resolve().parents[1] / "data" / "quran.json"
pytestmark = pytest.mark.skipif(not QURAN.exists(), reason="quran.json is built by fetch_assets.py")


@pytest.fixture(scope="module")
def ret():
    return ask.Retriever(search.build_index(json.loads(QURAN.read_text(encoding="utf-8"))))


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")
    monkeypatch.setenv("LLM_MODEL", "gemini-test")
    monkeypatch.setattr(ask, "BACKOFF", 0)
    monkeypatch.setitem(ask._cfg, "url", "")  # in-memory limits
    monkeypatch.setitem(ask._cfg, "enabled", True)
    monkeypatch.setitem(ask._cfg, "user_limit", 5)
    monkeypatch.setitem(ask._cfg, "global_limit", 200)
    ask._mem.clear()


def fake_llm(monkeypatch, text):
    monkeypatch.setattr(ask, "call_llm", lambda q, a: text)


def test_retrieval_finds_patience(ret):
    refs = [r["ref"] for r in (ask.ayah_public(x) for x in ret.top("What does the Quran say about patience?"))]
    assert len(refs) == 8 and "70:5" in refs


def test_good_answer_keeps_cited_ref_and_strips_fake_one(ret, monkeypatch):
    top = ask.ayah_public(ret.top("patience")[0])["ref"]
    fake_llm(monkeypatch, f"Be patient [{top}] and see [999:999].")
    out = ask.answer("patience", ret, "u1")
    assert out["status"] == "ok" and out["cited"] == [top] and "999:999" not in out["answer"]


def test_answer_without_citation_rejected(ret, monkeypatch):
    fake_llm(monkeypatch, "Patience is good.")
    assert ask.answer("patience", ret, "u1")["status"] == "search_only"


def test_only_fake_citation_rejected(ret, monkeypatch):
    fake_llm(monkeypatch, "Patience is good [114:114].")
    out = ask.answer("patience", ret, "u1")
    assert out["status"] == "search_only" and out["ayat"]


def test_fiqh_refused_without_llm_call(ret, monkeypatch):
    monkeypatch.setattr(ask, "call_llm", lambda *a: pytest.fail("LLM must not be called"))
    out = ask.answer("Is interest on a mortgage halal or haram?", ret, "u1")
    assert out["status"] == "refused" and "qualified scholar" in out["message"] and out["ayat"] is not None


def test_model_refusal(ret, monkeypatch):
    fake_llm(monkeypatch, "REFUSE: nope. Consult a qualified scholar.")
    assert ask.answer("patience", ret, "u1")["status"] == "refused"


def test_no_key_degrades_to_search_only(ret, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    out = ask.answer("patience", ret, "u1")
    assert out["status"] == "search_only" and len(out["ayat"]) == 8


def test_kill_switch(ret, monkeypatch):
    monkeypatch.setitem(ask._cfg, "enabled", False)
    assert ask.answer("patience", ret, "u1")["status"] == "search_only"


class R:
    def __init__(self, code, body=None):
        self.status_code, self._b, self.ok = code, body or {}, code < 400

    def json(self):
        return self._b


@pytest.mark.parametrize("code,status,msg", [(429, "limit", "Daily limit reached"), (402, "limit", "Daily limit"), (404, "search_only", "unavailable")])
def test_http_errors_degrade(ret, monkeypatch, code, status, msg):
    monkeypatch.setattr(ask.requests, "post", lambda *a, **k: R(code))
    out = ask.answer("patience", ret, "u1")
    assert out["status"] == status and msg in out["message"] and out["ayat"]
    assert ask._mem[(ask._today(), "u1")] == 0  # refunded


def test_timeout_retries_then_degrades(ret, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.Timeout()
    monkeypatch.setattr(ask.requests, "post", boom)
    assert ask.answer("patience", ret, "u1")["status"] == "search_only" and len(calls) == 3


def test_payload_has_no_user_id_or_key_in_body(ret, monkeypatch):
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(body=json, headers=headers, timeout=timeout)
        return R(200, {"candidates": [{"content": {"parts": [{"text": "x [70:5]"}]}}]})
    monkeypatch.setattr(ask.requests, "post", post)
    ask.answer("patience", ret, "secret-user-id-42")
    blob = str(seen["body"])
    assert "secret-user-id-42" not in blob and "test-key-123" not in blob
    assert seen["timeout"] == 15 and seen["body"]["generationConfig"]["maxOutputTokens"] == ask.MAX_TOKENS


def test_per_user_and_global_limits(ret, monkeypatch):
    fake_llm(monkeypatch, "ok [70:5]")
    monkeypatch.setitem(ask._cfg, "user_limit", 2)
    monkeypatch.setitem(ask._cfg, "global_limit", 3)
    st = [ask.answer("patience", ret, "a")["status"] for _ in range(3)]
    assert st == ["ok", "ok", "limit"]
    assert ask.answer("patience", ret, "b")["status"] != "limit"   # global 3rd call
    assert ask.answer("patience", ret, "c")["status"] == "limit"   # global exhausted


def test_endpoint_requires_login_and_works(client, monkeypatch):
    monkeypatch.setattr(main, "_retriever", lambda: ask.Retriever(search.build_index(json.loads(QURAN.read_text(encoding="utf-8")))))
    fake_llm(monkeypatch, "Be patient [70:5].")
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE_USER
    r = client.post("/api/ask", json={"question": "patience"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert client.post("/api/ask", json={"question": "x"}).status_code == 400


def test_ask_rate_limit_message_says_minute(client, monkeypatch):
    monkeypatch.setattr(main, "_retriever", lambda: ask.Retriever(search.build_index(json.loads(QURAN.read_text(encoding="utf-8")))))
    fake_llm(monkeypatch, "x [70:5]")
    main.ATTEMPTS.clear()
    codes = [client.post("/api/ask", json={"question": "patience"}) for _ in range(11)]
    assert codes[9].status_code == 200 and codes[10].status_code == 429
    assert "a minute" in codes[10].json()["detail"] and "15" not in codes[10].json()["detail"]
