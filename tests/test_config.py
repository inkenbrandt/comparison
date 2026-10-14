from pathlib import Path

import pytest

from etcompare.config import (
    ET_UNIT,
    ExtractedTableSpec,
    FootprintSetSpec,
    ProductSpec,
    StationSpec,
    StationTableSpec,
    config_from_dict,
    load_config,
)
from etcompare.errors import ContractError

EXAMPLE = Path(__file__).parents[1] / "examples" / "us_utj.yaml"


def station(**kw):
    base = dict(site_id="US-UTJ", timezone="UTC-07:00", day_definition="local_standard_time",
                interval_label="start")
    base.update(kw)
    return StationSpec(**base)


def station_table(**kw):
    base = dict(site_id="US-UTJ", date_column="date",
                reference_columns={"et_raw": "ET", "et_corrected": "ET_corr"},
                gap_flag_column="ET_gap", units={"ET": "mm/day", "ET_corr": "mm/day"})
    base.update(kw)
    return StationTableSpec(**base)


def extracted(**kw):
    base = dict(name="t", site_column="station_id", model_column="model",
                date_column="footprint_date",
                value_columns={"ET_fp": "upstream_footprint_weighted"},
                units={"ET_fp": "mm/day"}, footprint_set_id="A",
                footprint_method="et_weighted_unverified")
    base.update(kw)
    return ExtractedTableSpec(**base)


def test_valid_station():
    assert station().timezone == "UTC-07:00"


@pytest.mark.parametrize("field", ["day_definition", "interval_label", "timezone"])
def test_station_missing_day_semantics(field):
    with pytest.raises(ContractError) as ei:
        station(**{field: None})
    msg = str(ei.value)
    assert f"station US-UTJ: {field} not declared" in msg
    assert f"stations.US-UTJ.{field}" in msg


def test_station_bad_values():
    with pytest.raises(ContractError, match="not one of"):
        station(day_definition="local")
    with pytest.raises(ContractError, match="fixed offset"):
        station(timezone="Mountain-ish")


@pytest.mark.parametrize("units", [None, "", "mm", "in/day", "W m-2"])
def test_product_units_must_be_declared_mm_per_day(units):
    with pytest.raises(ContractError, match="ET units"):
        ProductSpec("ssebop", units=units, day_definition="unknown",
                    temporal_support="interpolated_daily")


def test_product_unknown_allowed_but_mandatory():
    p = ProductSpec("ssebop", units="mm/day", day_definition="unknown",
                    temporal_support="unknown")
    assert p.units == ET_UNIT
    with pytest.raises(ContractError, match="temporal_support not declared"):
        ProductSpec("ssebop", units="mm/day", day_definition="unknown", temporal_support=None)


def test_station_table_contract_messages():
    with pytest.raises(ContractError, match="has no site column; pass site_id="):
        station_table(site_id=None)
    with pytest.raises(ContractError, match="reference variant 'et_corrected' has no source"):
        station_table(reference_columns={"et_raw": "ET"})
    with pytest.raises(ContractError,
                       match=r"ET units for column 'ET_corr' not declared; magnitude is not"):
        station_table(units={"ET": "mm/day"})
    assert dict(station_table().units) == {"ET": ET_UNIT, "ET_corr": ET_UNIT}


def test_extracted_needs_method_label_and_denominator():
    with pytest.raises(ContractError, match="column 'ET_mean' needs an extraction_method label"):
        extracted(value_columns={"ET_mean": None}, units={"ET_mean": "mm/day"})
    with pytest.raises(ContractError, match="denominator"):
        extracted(retained_weight_column="valid_weight_fraction")
    with pytest.raises(ContractError, match="unique"):
        extracted(value_columns={"a": "m", "b": "m"}, units={"a": "mm/day", "b": "mm/day"})


def test_footprint_semantics_mandatory():
    with pytest.raises(ContractError, match="footprint value semantics not declared"):
        FootprintSetSpec("A", Path("."), weighting_method="x", value_semantics=None)
    with pytest.raises(ContractError, match="weighting_method not declared"):
        FootprintSetSpec("A", Path("."), weighting_method=None,
                         value_semantics="normalized_cell_weight")


def test_load_example_config():
    cfg = load_config(EXAMPLE)
    assert set(cfg.products) == {"disalexi", "eemetric", "ensemble", "geesebal", "ptjpl",
                                 "sims", "ssebop"}
    assert cfg.model_registry().ensembles == {"ensemble"}
    (st,) = cfg.station_tables
    assert st.path.name == "US-UTJ_daily.csv" and st.path.is_absolute()
    (ex,) = cfg.extracted_tables
    assert ex.value_columns["ET_mean"] == "source_region_mean"
    assert cfg.footprint_sets[0].recursive is False


def test_unknown_keys_rejected():
    with pytest.raises(ContractError, match="unknown configuration key"):
        config_from_dict({"stationz": {}})
    with pytest.raises(ContractError, match=r"stations\.X"):
        config_from_dict({"stations": {"X": {"timezone": "UTC", "day_definition": "civil_time",
                                             "interval_label": "end", "tz": 1}}})


def test_station_table_requires_station_declaration():
    with pytest.raises(ContractError, match="no station declaration"):
        config_from_dict({"station_tables": [{
            "site_id": "US-UTJ", "date_column": "date", "gap_flag_column": "ET_gap",
            "reference_columns": {"et_raw": "ET", "et_corrected": "ET_corr"},
            "units": {"ET": "mm/day", "ET_corr": "mm/day"}}]})


def test_raster_scaling_contract():
    from etcompare.config import ProductSpec

    p = ProductSpec("ssebop", units="mm/day", day_definition="unknown",
                    temporal_support="interpolated_daily")
    with pytest.raises(ContractError, match="raster scaling unknown"):
        p.require_scaling()
    with pytest.raises(ContractError, match="scale_factor not declared"):
        ProductSpec("ssebop", units="mm/day", day_definition="unknown",
                    temporal_support="interpolated_daily", raster_scaling="declared")
    with pytest.raises(ContractError, match="not one of"):
        ProductSpec("ssebop", units="mm/day", day_definition="unknown",
                    temporal_support="interpolated_daily", raster_scaling="guess")


def test_raster_extraction_spec():
    from etcompare.config import RasterExtractionSpec

    ok = RasterExtractionSpec("x", "US-UTJ", methods=["pixel_window"], window_sizes=[3, 5],
                              start="2024-06-05", end="2025-10-09")
    assert ok.window_sizes == (3, 5) and ok.start == "2024-06-05"
    for kw, match in [
        (dict(methods=["footprint"]), "footprint_set_id"),
        (dict(methods=["bilinear"]), "unknown method"),
        (dict(methods=["pixel_window"], window_sizes=[2]), "odd"),
        (dict(methods=["pixel_window"], start="2025-13-01"), "ISO"),
        (dict(methods=["pixel_window"], start="2025-02-01", end="2025-01-01"), "after"),
        (dict(methods=[]), "no extraction methods"),
    ]:
        with pytest.raises(ContractError, match=match):
            RasterExtractionSpec("x", "US-UTJ", **kw)


def test_example_raster_extraction_declared():
    cfg = load_config(EXAMPLE)
    x, m = cfg.raster_extractions
    assert x.footprint_set_id == "US-UTJ_set_B_etw_20261012"
    fs = cfg.footprint_set(x.footprint_set_id)
    assert fs.domain_capture_column == "et_weighted_coverage_pct"
    assert fs.temporal_weighting == "unknown" and fs.target_dependence == "unknown"
    assert cfg.products["sims"].nodata == -9999.0
    mean = cfg.footprint_set(m.footprint_set_id)
    assert mean.temporal_weighting == "time_mean" and mean.target_dependence == "no"
    assert m.negative_weight_tolerance == x.negative_weight_tolerance  # same rules (step 6)


def test_footprint_set_temporal_weighting_vocabulary():
    with pytest.raises(ContractError, match="temporal_weighting"):
        FootprintSetSpec("S", "x", weighting_method="m", value_semantics="cell_contribution",
                         temporal_weighting="nldas")
