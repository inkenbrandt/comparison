"""Integration checks on the user's US-UTJ example files (local only, never distributed).

Counts are regression checks for these exact unfiltered files
(PROJECT_CONTEXT.md sections 5-6, input_contract.md), not library requirements.
Set ETCOMPARE_DATA_DIR to the folder holding the files; defaults to the repo root.
"""

import os
from pathlib import Path

import pytest

from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.config import load_config
from etcompare.io.tables import read_table, write_table
from etcompare.registry import SiteRegistry

ROOT = Path(__file__).parents[2]
DATA = Path(os.environ.get("ETCOMPARE_DATA_DIR", ROOT))
CSV = DATA / "US-UTJ_daily.csv"
PARQUET = DATA / "station_oet_US-UTJ.parquet"
CSV_SHA = "3924e02d30d2e8aed8daea30fc56fd9b2c002031f7fa53e8f378bc8e3652c60f"
PARQUET_SHA = "e0d5c1afff7d7ca5663c1ded28e0705302754b9cbea44d562f84eae0ae544743"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (CSV.exists() and PARQUET.exists()),
                       reason="US-UTJ example files not present"),
]


@pytest.fixture(scope="module")
def cfg():
    return load_config(ROOT / "examples" / "us_utj.yaml")


@pytest.fixture(scope="module")
def station(cfg):
    (spec,) = cfg.station_tables
    return adapt_station_daily(CSV, spec, cfg.stations["US-UTJ"])


@pytest.fixture(scope="module")
def extracted(cfg):
    (spec,) = cfg.extracted_tables
    return adapt_extracted_table(PARQUET, spec, models=cfg.model_registry(),
                                 sites=SiteRegistry(cfg.site_aliases), products=cfg.products,
                                 stations=cfg.stations)


def test_station_adapter(station):
    f = station.frame
    assert station.attributes["source_sha256"] == CSV_SHA
    assert len(f) == 492 and f["date"].is_unique
    assert station.attributes["reference_provenance_counts"] == {
        "corrected_from_raw": 303, "corrected_filled": 170, "reference_missing": 19,
        "inconsistent_flags": 0,
    }
    assert int(f["raw_available"].sum()) == 303
    assert int(f["gap_filled"].sum()) == 170
    closure = next(i for i in station.report if i.code == "closure_relation")
    assert closure.severity == "info" and closure.n_rows == 0  # ET_corr = ET*ebc_cf on 303/303
    assert next(i for i in station.report if i.code == "calendar").n_rows == 0
    assert len([c for c in f.columns if c.startswith("src_")]) == 44
    assert station.report.ok and len(station.exclusions) == 0


def test_extracted_adapter(extracted):
    f = extracted.frame
    assert extracted.attributes["source_sha256"] == PARQUET_SHA
    assert len(f) == 2 * 2336
    assert extracted.report.ok
    assert {"model_asset_consistent", "footprint_asset_consistent"} <= extracted.report.codes()
    assert "weight_sum_not_unity" not in extracted.report.codes()
    assert set(f["day_alignment"]) == {"unverified"}  # OpenET day definition is unknown

    fp = f[f["extraction_method"] == "upstream_footprint_weighted"]
    cov = fp.groupby("model").size().to_dict()
    assert cov == {"eemetric": 431, "ensemble": 431, "ssebop": 431, "disalexi": 261,
                   "geesebal": 261, "ptjpl": 261, "sims": 260}
    low = fp[fp["fp_weight_retained_frac"] < 0.90]
    assert len(low) == 180 and set(low["model"]) == {"sims"}
    assert (fp["fp_weight_sum"] - 1).abs().max() <= 1.01e-8


def test_cross_table_regression_counts(station, extracted):
    st = station.frame.set_index("date")
    fp = extracted.frame[extracted.frame["extraction_method"] == "upstream_footprint_weighted"]
    extracted_dates = set(fp["date"])
    assert len(set(st.index) - extracted_dates) == 61
    per_model = fp.groupby("model")["date"].apply(set)
    common = set.intersection(*per_model)
    assert len(common) == 260
    prov = st.loc[sorted(common), "reference_provenance"].value_counts().to_dict()
    assert prov == {"corrected_from_raw": 172, "corrected_filled": 88}


def test_round_trip_real_tables(station, extracted, tmp_path):
    for result, name in ((station, "station.parquet"), (extracted, "extractions.parquet")):
        back = read_table(write_table(result.table, tmp_path / name))
        assert back.frame.equals(result.frame)
        assert back.attributes["source_sha256"] == result.attributes["source_sha256"]


OPENET_ROOT = os.environ.get("ETCOMPARE_OPENET_ROOT")


@pytest.mark.skipif(not OPENET_ROOT, reason="set ETCOMPARE_OPENET_ROOT to index real rasters")
def test_discover_real_openet_rasters(cfg):
    from etcompare.io.manifest import discover_openet_rasters

    r = discover_openet_rasters(OPENET_ROOT, models=cfg.model_registry(), products=cfg.products,
                                on_unparseable="skip", on_conflict="report")
    print(r.report.summary())
    assert len(r.manifest) > 0
