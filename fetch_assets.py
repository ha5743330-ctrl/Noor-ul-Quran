#!/usr/bin/env python3
"""Fetch Quran text with full Uthmani Tashkeel and translations."""
import json, requests
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"

def fetch_edition(edition):
    response = requests.get(f"https://api.alquran.cloud/v1/quran/{edition}", timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200 or not isinstance(payload.get("data", {}).get("surahs"), list):
        raise RuntimeError(f"Quran text download returned an invalid response for {edition}.")
    return payload["data"]["surahs"]


def build_quran_data(arabic_surahs, urdu_surahs, english_surahs):
    if not (len(arabic_surahs) == len(urdu_surahs) == len(english_surahs) == 114):
        raise RuntimeError("Quran text download did not contain all 114 surahs.")

    quran_data = {
        "english_translation": (
            "The Meaning of the Glorious Koran; translator: Marmaduke William Pickthall; "
            "source: AlQuran Cloud API edition en.pickthall; public domain in the USA"
        ),
        "surahs": {},
    }

    for expected_surah, (ar_surah, ur_surah, en_surah) in enumerate(
        zip(arabic_surahs, urdu_surahs, english_surahs),
        start=1,
    ):
        if not (
            ar_surah["number"] == ur_surah["number"] == en_surah["number"] == expected_surah
            and len(ar_surah["ayahs"]) == len(ur_surah["ayahs"]) == len(en_surah["ayahs"])
            and len(ar_surah["ayahs"]) > 0
        ):
            raise RuntimeError(f"Quran editions do not match for surah {expected_surah}.")
        s_num = str(expected_surah)
        quran_data["surahs"][s_num] = {
            "name": ar_surah["name"],
            "english": ar_surah["englishName"],
            "n": len(ar_surah["ayahs"]),
            "arabic": {},
            "ayahs": {},
            "english_ayahs": {},
        }

        for expected_ayah, (ar_ayah, ur_ayah, en_ayah) in enumerate(
            zip(ar_surah["ayahs"], ur_surah["ayahs"], en_surah["ayahs"]),
            start=1,
        ):
            texts = (ar_ayah.get("text"), ur_ayah.get("text"), en_ayah.get("text"))
            if not (
                ar_ayah.get("numberInSurah")
                == ur_ayah.get("numberInSurah")
                == en_ayah.get("numberInSurah")
                == expected_ayah
                and all(isinstance(text, str) and text.strip() for text in texts)
            ):
                raise RuntimeError(
                    f"Quran editions do not match or contain empty text at "
                    f"surah {s_num}, ayah {expected_ayah}."
                )
            a_num = str(expected_ayah)
            quran_data["surahs"][s_num]["arabic"][a_num] = ar_ayah["text"]
            quran_data["surahs"][s_num]["ayahs"][a_num] = ur_ayah["text"]
            quran_data["surahs"][s_num]["english_ayahs"][a_num] = en_ayah["text"]
    return quran_data


def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("Quran text (Full Uthmani Tashkeel ke sath) download ho raha hai...")
    # Uthmani Arabic, Urdu Jalandhry, and the public-domain Pickthall translation.
    arabic_surahs = fetch_edition("quran-uthmani")
    urdu_surahs = fetch_edition("ur.jalandhry")
    english_surahs = fetch_edition("en.pickthall")
    quran_data = build_quran_data(arabic_surahs, urdu_surahs, english_surahs)
    output_path = DATA_DIR / "quran.json"
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(quran_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print("`data/quran.json` 100% accuracy ke sath download ho kar save ho gaya hai!")


if __name__ == "__main__":
    main()