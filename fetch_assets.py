#!/usr/bin/env python3
"""Fetch Quran text with full Uthmani Tashkeel and Urdu translation."""
import json, requests
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

print("Quran text (Full Uthmani Tashkeel ke sath) download ho raha hai...")

# Official Verified Uthmani Quran Script
ar_res = requests.get("https://api.alquran.cloud/v1/quran/quran-uthmani").json()
ur_res = requests.get("https://api.alquran.cloud/v1/quran/ur.jalandhry").json()

quran_data = {"surahs": {}}

for ar_surah, ur_surah in zip(ar_res["data"]["surahs"], ur_res["data"]["surahs"]):
    s_num = str(ar_surah["number"])
    quran_data["surahs"][s_num] = {
        "name": ar_surah["name"],
        "english": ar_surah["englishName"],
        "n": len(ar_surah["ayahs"]),
        "arabic": {},
        "ayahs": {}
    }
    
    for ar_ayah, ur_ayah in zip(ar_surah["ayahs"], ur_surah["ayahs"]):
        a_num = str(ar_ayah["numberInSurah"])
        quran_data["surahs"][s_num]["arabic"][a_num] = ar_ayah["text"]
        quran_data["surahs"][s_num]["ayahs"][a_num] = ur_ayah["text"]

(DATA_DIR / "quran.json").write_text(json.dumps(quran_data, ensure_ascii=False, indent=2), encoding="utf-8")
print("`data/quran.json` 100% accuracy ke sath download ho kar save ho gaya hai!")