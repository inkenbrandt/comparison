"""The user's US-UTJ zone-E crop-height file (local only, never distributed)."""

from pathlib import Path

import pytest

from etcompare.adapters.crop_height import adapt_crop_height, growing_season_windows
from etcompare.config import load_config
from etcompare.provenance import file_sha256

ROOT = Path(__file__).parents[2]

CFG = load_config(ROOT / "examples" / "us_utj.yaml")
SPEC = CFG.crop_height_table("utj_zone_e_crop_height")
SHA = "ab8c3cc2c56a04a4efd82fa736ef237f667a9c1e2c8288ca83109ff5d22d1a48"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SPEC.path is None or not SPEC.path.exists(),
                       reason="US-UTJ crop-height file not present"),
]


def test_us_utj_growing_seasons_from_crop_height():
    assert file_sha256(SPEC.path) == SHA
    r = adapt_crop_height(SPEC.path, SPEC)
    assert r.report.ok and len(r.table) == 730
    assert [(w.label, w.start, w.end) for w in growing_season_windows(r)] == [
        ("GS2024", "2024-03-15", "2024-12-06"), ("GS2025", "2025-03-15", "2025-12-06")]
    f = r.table.frame
    assert int(f["in_season"].sum()) == 2 * 267
    assert set(f.loc[~f["in_season"], "height_cm"]) == {5.0}
