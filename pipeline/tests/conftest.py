import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    """The real city config (city.json), which the tests also check."""
    from common import city

    return city()


@pytest.fixture
def overpass():
    return json.loads((FIXTURES / "overpass_small.json").read_text(encoding="utf-8"))
