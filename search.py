"""Plain substring search over Arabic / Urdu / English ayah text (no models, ~6k rows)."""
import re

_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640\u200c\u200d\ufeff\xa0]")
_MAP = str.maketrans({"ٱ": "ا", "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ی": "ي", "ې": "ي", "ك": "ک", "ة": "ه", "ہ": "ه", "ۃ": "ه", "ە": "ه", "ھ": "ه", "ؤ": "و"})
_TRAIL_NUM = re.compile(r"[\s\xa0]*[\u0660-\u0669\u06f0-\u06f9]+$")
REF = re.compile(r"^\s*(\d{1,3})\s*[:.]\s*(\d{1,3})\s*$")


def norm(text):
    return _MARKS.sub("", text).translate(_MAP).casefold()


def build_index(quran):
    rows = []
    for s_key, s in quran["surahs"].items():
        for a_key, ar in s["arabic"].items():
            ar = _TRAIL_NUM.sub("", ar)
            ur, en = s["ayahs"].get(a_key, ""), s["english_ayahs"].get(a_key, "")
            rows.append({
                "surah": int(s_key), "ayah": int(a_key), "surah_name": s["english"],
                "arabic": ar, "urdu": ur, "english": en, "_k": (norm(ar), norm(ur), norm(en)),
            })
    rows.sort(key=lambda r: (r["surah"], r["ayah"]))
    return rows


def _public(row):
    s, a = row["surah"], row["ayah"]
    out = {k: v for k, v in row.items() if k != "_k"}
    out.update(ref=f"{s}:{a}", tafsir_url=f"https://quran.com/{s}/{a}/tafsirs", generate_url=f"/generate.html?pick={s}:{a}-{a}")
    return out


def search(index, query, limit=30):
    """Returns (total, results). A bare 'S:A' reference jumps to that ayah."""
    ref = REF.match(query)
    if ref:
        s, a = int(ref[1]), int(ref[2])
        hit = [r for r in index if r["surah"] == s and r["ayah"] == a]
        return len(hit), [_public(r) for r in hit]
    terms = [t for t in norm(query).split() if t]
    if not terms or sum(map(len, terms)) < 2:
        return 0, []
    hits = [r for r in index if any(all(t in field for t in terms) for field in r["_k"])]
    return len(hits), [_public(r) for r in hits[:limit]]
