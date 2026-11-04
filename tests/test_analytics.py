import analytics


def setup_function(_):
    analytics._counts.clear(); analytics._secs.clear(); analytics._visitors.clear(); analytics._seen_signups.clear()
    analytics.configure("http://sb.test", "svc-key")


def test_bots_and_empty_ua_are_excluded():
    for ua in ("Googlebot/2.1", "curl/8", "python-requests/2", "HeadlessChrome", ""):
        analytics.page_view("1.2.3.4", ua)
    assert not analytics._counts
    analytics.page_view("1.2.3.4", "Mozilla/5.0 Firefox/120")
    assert sum(c["views"] for c in analytics._counts.values()) == 1


def test_hash_is_salted_per_day_and_hides_ip():
    a = analytics.visitor_hash("9.9.9.9", "UA", "2026-11-04")
    assert a != analytics.visitor_hash("9.9.9.9", "UA", "2026-11-05")
    assert "9.9.9.9" not in a and len(a) == 32


def test_flush_sends_and_requeues_on_failure(monkeypatch):
    sent = []

    class R:
        def raise_for_status(self): pass

    monkeypatch.setattr(analytics.requests, "post", lambda url, json, **k: sent.append(json) or R())
    analytics.page_view("1.1.1.1", "Mozilla/5.0"); analytics.signup("u1"); analytics.signup("u1")
    analytics.render_done(2, 1, 30.0)
    analytics.flush()
    body = sent[0]
    assert body["p_views"] == 1 and body["p_signups"] == 1 and body["p_videos"] == 2
    assert body["p_failures"] == 1 and body["p_render_secs"] == 30.0 and len(body["p_visitors"]) == 1
    assert not analytics._counts

    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(analytics.requests, "post", boom)
    analytics.page_view("1.1.1.1", "Mozilla/5.0")
    analytics.flush()  # must not raise
    assert sum(c["views"] for c in analytics._counts.values()) == 1


def test_disabled_without_supabase_is_noop():
    analytics.configure("", "")
    analytics.page_view("1.1.1.1", "Mozilla/5.0"); analytics.flush()
    assert not analytics._counts and analytics.read_days() == []
