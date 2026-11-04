import re
from pathlib import Path

PUB = Path(__file__).resolve().parents[1] / "public"


def test_demo_assets_exist_and_small():
    files = list((PUB / "demo").iterdir())
    assert sum(f.stat().st_size for f in files) <= 15 * 1024 * 1024
    reels = [f for f in files if f.suffix == ".mp4"]
    images = [f for f in files if f.name.startswith("ayah-")]
    assert len(reels) == 3 and len(images) == 6
    assert all(f.stat().st_size <= 3 * 1024 * 1024 for f in reels)


def test_index_references_only_existing_demo_files():
    html = (PUB / "index.html").read_text(encoding="utf-8")
    refs = set(re.findall(r'/demo/[\w.-]+', html))
    assert len(refs) >= 12
    assert all((PUB / r.lstrip("/")).exists() for r in refs)
