"""Static checks for the public legal pages and the contact-email config (no browser needed)."""
import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parents[1] / "public"


def _read(name):
    return (PUBLIC / name).read_text(encoding="utf-8")


def _last_updated(html):
    return re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", html).group(1)


def test_privacy_and_terms_show_the_same_last_updated_date():
    assert _last_updated(_read("privacy.html")) == _last_updated(_read("terms.html"))


def test_no_example_com_fallback_in_public_pages_or_scripts():
    for name in ("privacy.html", "terms.html", "index.html", "legal.js"):
        assert "example.com" not in _read(name), name


def test_contact_lines_stay_hidden_until_the_configured_email_is_applied():
    for name in ("privacy.html", "terms.html"):
        html = _read(name)
        assert 'class="contact-line"' in html or 'class="contact-line ' in html or "contact-line\" hidden" in html
        assert not re.search(r'<[^>]+class="contact-email[^>]*href="mailto:', html), name
    script = _read("legal.js")
    assert "NOOR_CONTACT_EMAIL" in script
    assert not re.search(r"[\w.-]+@[\w-]+\.[a-z]{2,}", script)


def test_privacy_lists_the_urdu_translation_sources():
    html = _read("privacy.html")
    assert "Jalandhry" in html and "Usmani" in html


def test_terms_cover_social_media_tafseer_and_mistake_reports():
    html = _read("terms.html")
    assert "may post the reels you generate on social media" in html
    assert "does <strong>not</strong> replace authentic tafseer" in html
    assert "report it by email" in html
    assert "سوشل میڈیا" in html and "تفسیر" in html
