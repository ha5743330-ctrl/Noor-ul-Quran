#!/usr/bin/env python3
"""Load verified QPC Hafs Quran text and fetch its translations if needed."""
import hashlib
import json
import requests
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
QPC_SOURCE_PATH = DATA_DIR / "quran_qpc_hafs.json"
QPC_SHA256_PATH = DATA_DIR / "quran_qpc_hafs.sha256"
QPC_SOURCE_URL = (
    "https://api.quran.com/api/v4/quran/verses/qpc_hafs"
    "?chapter_number={chapter_number}&per_page=300"
)


def fetch_alquran_cloud_edition(edition):
    response = requests.get(f"https://api.alquran.cloud/v1/quran/{edition}", timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200 or not isinstance(payload.get("data", {}).get("surahs"), list):
        raise RuntimeError(f"Quran text download returned an invalid response for {edition}.")
    return payload["data"]["surahs"]


def fetch_qpc_hafs():
    surahs = {}
    for chapter in range(1, 115):
        response = requests.get(
            QPC_SOURCE_URL.format(chapter_number=chapter),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        verses = payload.get("verses")
        if not isinstance(verses, list):
            raise RuntimeError(f"QPC Hafs source is invalid for surah {chapter}.")
        chapter_ayahs = {}
        for verse in verses:
            verse_key = verse.get("verse_key", "").split(":")
            text = verse.get("text_qpc_hafs")
            if (
                len(verse_key) != 2
                or int(verse_key[0]) != chapter
                or not isinstance(text, str)
                or not text
            ):
                raise RuntimeError(f"Invalid QPC Hafs verse in surah {chapter}.")
            chapter_ayahs[verse_key[1]] = text
        if not chapter_ayahs:
            raise RuntimeError(f"QPC Hafs source is empty for surah {chapter}.")
        total_records = payload.get("pagination", {}).get("total_records")
        if total_records is not None and len(chapter_ayahs) != total_records:
            raise RuntimeError(f"Incomplete QPC Hafs source for surah {chapter}.")
        surahs[str(chapter)] = chapter_ayahs
    return surahs


def source_bytes(surahs):
    source = {
        "edition": "text_qpc_hafs",
        "source": QPC_SOURCE_URL,
        "surahs": surahs,
    }
    return (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_qpc_source():
    try:
        source_data = json.loads(QPC_SOURCE_PATH.read_text(encoding="utf-8"))
        expected_hash = QPC_SHA256_PATH.read_text(encoding="ascii").split()[0]
        actual_hash = hashlib.sha256(QPC_SOURCE_PATH.read_bytes()).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError("Verified QPC Hafs source and SHA-256 are required.") from exc
    if source_data.get("edition") != "text_qpc_hafs" or expected_hash != actual_hash:
        raise RuntimeError("QPC Hafs source checksum or edition is invalid.")
    surahs = source_data.get("surahs")
    if not isinstance(surahs, dict) or len(surahs) != 114:
        raise RuntimeError("QPC Hafs source must contain all 114 surahs.")
    if sum(len(ayahs) for ayahs in surahs.values()) != 6236:
        raise RuntimeError("QPC Hafs source must contain all 6,236 ayahs.")
    return surahs


def write_qpc_source(surahs):
    source_data = source_bytes(surahs)
    temporary_path = QPC_SOURCE_PATH.with_suffix(".json.tmp")
    temporary_path.write_bytes(source_data)
    temporary_path.replace(QPC_SOURCE_PATH)
    checksum = hashlib.sha256(source_data).hexdigest()
    QPC_SHA256_PATH.write_text(f"{checksum}  {QPC_SOURCE_PATH.name}\n", encoding="ascii")


def build_quran_data(qpc_hafs_surahs, urdu_surahs, english_surahs):
    if not (
        isinstance(qpc_hafs_surahs, dict)
        and len(qpc_hafs_surahs) == len(urdu_surahs) == len(english_surahs) == 114
    ):
        raise RuntimeError("Quran editions did not contain all 114 surahs.")

    quran_data = {
        "english_translation": (
            "The Meaning of the Glorious Koran; translator: Marmaduke William Pickthall; "
            "source: AlQuran Cloud API edition en.pickthall; public domain in the USA"
        ),
        "surahs": {},
    }

    for expected_surah, (ur_surah, en_surah) in enumerate(
        zip(urdu_surahs, english_surahs),
        start=1,
    ):
        qpc_ayahs = qpc_hafs_surahs.get(str(expected_surah), {})
        if not (
            ur_surah["number"] == en_surah["number"] == expected_surah
            and len(ur_surah["ayahs"]) == len(en_surah["ayahs"]) == len(qpc_ayahs)
            and len(qpc_ayahs) > 0
        ):
            raise RuntimeError(f"Quran editions do not match for surah {expected_surah}.")
        s_num = str(expected_surah)
        quran_data["surahs"][s_num] = {
            "name": ur_surah["name"],
            "english": ur_surah["englishName"],
            "n": len(qpc_ayahs),
            "arabic": {},
            "ayahs": {},
            "english_ayahs": {},
        }

        for expected_ayah, (ur_ayah, en_ayah) in enumerate(
            zip(ur_surah["ayahs"], en_surah["ayahs"]),
            start=1,
        ):
            a_num = str(expected_ayah)
            arabic_text = qpc_ayahs.get(a_num)
            texts = (arabic_text, ur_ayah.get("text"), en_ayah.get("text"))
            if not (
                ur_ayah.get("numberInSurah")
                == en_ayah.get("numberInSurah")
                == expected_ayah
                and all(isinstance(text, str) and text.strip() for text in texts)
            ):
                raise RuntimeError(
                    f"Quran editions do not match or contain empty text at "
                    f"surah {s_num}, ayah {expected_ayah}."
                )
            quran_data["surahs"][s_num]["arabic"][a_num] = arabic_text
            quran_data["surahs"][s_num]["ayahs"][a_num] = ur_ayah["text"]
            quran_data["surahs"][s_num]["english_ayahs"][a_num] = en_ayah["text"]
    return quran_data


def main():
    DATA_DIR.mkdir(exist_ok=True)
    output_path = DATA_DIR / "quran.json"
    qpc_hafs_surahs = validate_qpc_source() if QPC_SHA256_PATH.exists() else None
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8-sig"))
        surahs = existing.get("surahs") if isinstance(existing, dict) else None
        if not isinstance(surahs, dict) or len(surahs) != 114 or qpc_hafs_surahs is None:
            raise RuntimeError("Existing Quran data is invalid; refusing to overwrite it.")
        if any(
            surah.get("arabic") != qpc_hafs_surahs.get(number)
            for number, surah in surahs.items()
        ):
            raise RuntimeError("Quran Arabic text does not match verified QPC Hafs source.")
        for number, surah in surahs.items():
            ayahs = surah.get("arabic") if isinstance(surah, dict) else None
            if not isinstance(ayahs, dict) or len(ayahs) != surah.get("n"):
                raise RuntimeError(
                    f"Existing Arabic Quran data is invalid for surah {number}; "
                    "refusing to overwrite it."
                )
        print("Existing checked-in Quran data retained; download skipped.")
        return

    print("QPC Hafs Quran text and translations loading...")
    if qpc_hafs_surahs is None:
        qpc_hafs_surahs = fetch_qpc_hafs()
        write_qpc_source(qpc_hafs_surahs)
        qpc_hafs_surahs = validate_qpc_source()
    urdu_surahs = fetch_alquran_cloud_edition("ur.jalandhry")
    english_surahs = fetch_alquran_cloud_edition("en.pickthall")
    quran_data = build_quran_data(qpc_hafs_surahs, urdu_surahs, english_surahs)
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(quran_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    print("`data/quran.json` 100% accuracy ke sath download ho kar save ho gaya hai!")


if __name__ == "__main__":
    main()