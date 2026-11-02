import pytest

import main


@pytest.fixture(autouse=True)
def _env_independent(monkeypatch):
    """Tests must not depend on the developer's local .env."""
    monkeypatch.setattr(main, "MAX_VIDEO_SECONDS", 35)
