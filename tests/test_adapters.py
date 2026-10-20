import numpy as np
import pandas as pd
import pytest

from etcompare.adapters.extracted import adapt_extracted_table
from etcompare.adapters.station import adapt_station_daily
from etcompare.config import ExtractedTableSpec, ProductSpec, StationSpec, StationTableSpec
from etcompare.errors import ContractError, DataValidationError, DuplicateKeyError, SchemaError
from etcompare.io.tables import read_table, write_table
from etcompare.registry import ModelRegistry

NAN = np.nan
STATION = StationSpec("US-XXX", timezone="UTC-07:00", day_definition="local_standard_time",
                      interval_label="start")
TABLE = StationTableSpec(site_id="US-XXX", date_column="date",
                         reference_columns={"et_raw": "ET", "et_corrected": "ET_corr"},
                         gap_flag_column="ET_gap", closure_factor_column="ebc_cf",
                         units={"ET": "mm/day", "ET_corr": "mm/day"})

# date, ET, ET_corr, ET_gap, ebc_cf, expected provenance
CASES = [
    ("2024-01-01", 2.0, 2.4, False, 1.2, "corrected_from_raw"),
    ("2024-01-02", NAN, 3.0, True, NAN, "corrected_filled"),
    ("2024-01-03", NAN, NAN, False, NAN, "reference_missing"),
    ("2024-01-04", 1.0, NAN, False, NAN, "inconsistent_flags"),
    ("2024-01-05", 1.0, 1.1, True, 1.1, "inconsistent_flags"),
    ("2024-01-06", NAN, 2.0, False, NAN, "inconsistent_flags"),
    ("2024-01-07", NAN, NAN, True, NAN, "reference_missing"),
    # 2024-01-08 absent from the calendar
    ("2024-01-09", -0.3, -0.36, False, 1.2, "corrected_from_raw"),
]


def station_frame():
    return pd.DataFrame(
        [c[:5] for c in CASES], columns=["date", "ET", "ET_corr", "ET_gap", "ebc_cf"]
    ).assign(DOY=lambda d: pd.to_datetime(d["date"]).dt.dayofyear)


def test_station_provenance_for_every_flag_combination():
    r = adapt_station_daily(station_frame(), TABLE, STATION)
    f = r.frame
    assert list(f["reference_provenance"]) == [c[5] for c in CASES]
    assert list(f["raw_available"]) == [not np.isnan(c[1]) for c in CASES]
    assert f["et_raw_mm"].iloc[-1] == -0.3  # never clipped
    assert set(f["site_id"]) == {"US-XXX"}
    assert {"src_date", "src_ET", "src_ET_corr", "src_ET_gap", "src_ebc_cf", "src_DOY"} <= set(f)
    codes = r.report.codes()
    assert {"inconsistent_flags", "gap_flag_without_fill", "negative_et", "calendar",
            "closure_relation"} <= codes
    assert r.report.ok
    cal = next(i for i in r.report if i.code == "calendar")
    assert cal.n_rows == 1
    closure = next(i for i in r.report if i.code == "closure_relation")
    assert closure.severity == "info" and closure.n_rows == 0
    assert len(r.exclusions) == 3
    assert r.attributes["reference_provenance_counts"]["inconsistent_flags"] == 3


def test_station_from_csv_with_text_flags(tmp_path):
    p = tmp_path / "daily.csv"
    station_frame().to_csv(p, index=False)
    r = adapt_station_daily(p, TABLE, STATION)
    assert r.attributes["source_sha256"] and len(r.frame) == len(CASES)
    back = read_table(write_table(r.table, tmp_path / "out.parquet"))
    pd.testing.assert_frame_equal(back.frame, r.frame)
    assert back.attributes["station"]["timezone"] == "UTC-07:00"


def test_station_closure_mismatch_is_a_warning():
    df = station_frame()
    df.loc[0, "ebc_cf"] = 1.3
    r = adapt_station_daily(df, TABLE, STATION)
    closure = next(i for i in r.report if i.code == "closure_relation")
    assert closure.severity == "warning" and closure.n_rows == 1


@pytest.mark.parametrize(
    "mutate, exc, match",
    [
        (lambda d: d.assign(date=d["date"].str.replace("-", "/")), DataValidationError,
         "unparseable_dates"),
        (lambda d: d.assign(ET_gap=["yes"] + [False] * (len(d) - 1)), DataValidationError,
         "non_boolean_flag"),
        (lambda d: d.assign(ET=["x"] + [1.0] * (len(d) - 1)), DataValidationError,
         "non_numeric"),
        (lambda d: d.drop(columns="ET_corr"), SchemaError, "ET_corr"),
        (lambda d: pd.concat([d, d.iloc[[0]]]), DuplicateKeyError, "duplicated key"),
    ],
)
def test_station_rejects(mutate, exc, match):
    with pytest.raises(exc, match=match):
        adapt_station_daily(mutate(station_frame()), TABLE, STATION)


def test_station_spec_mismatch():
    other = StationSpec("US-YYY", timezone="UTC", day_definition="civil_time",
                        interval_label="end")
    with pytest.raises(ContractError):
        adapt_station_daily(station_frame(), TABLE, other)


# --------------------------------------------------------------------------- extracted

MODELS = ModelRegistry.default()
EXSPEC = ExtractedTableSpec(
    name="t", site_column="station_id", model_column="model", date_column="footprint_date",
    value_columns={"ET_fp": "upstream_footprint_weighted", "ET_mean": "source_region_mean"},
    units={"ET_fp": "mm/day", "ET_mean": "mm/day"}, footprint_set_id="A",
    footprint_method="et_weighted_unverified", weight_sum_column="unity_check",
    retained_weight_column="valid_weight_fraction",
    retained_weight_denominator="normalized_truncated_footprint",
    model_asset_column="openet_file", footprint_asset_column="footprint_file",
    source_path_column="openet_path",
)


def extracted_frame():
    rows = []
    for model in ("ssebop", "sims"):
        for d in pd.date_range("2025-01-03", periods=3):
            ymd = d.strftime("%Y_%m_%d")
            fname = f"XX-CdM_US-XXX_{model}_et_{ymd}_abc12.tif"
            rows.append({
                "station_id": "US-XXX", "model": model,
                "footprint_file": f"ffp_US-XXX_daily_etw_{d:%Y%m%d}",
                "openet_file": fname, "openet_path": f"/content/drive/{model}/{fname}",
                "footprint_date": d, "ET_fp": 1.0 + 0.1 * len(rows), "ET_mean": 2.0,
                "unity_check": 1.0 + 1e-9, "valid_weight_fraction": 0.5 if model == "sims" else 1,
            })
    return pd.DataFrame(rows)


def test_extracted_long_format():
    r = adapt_extracted_table(extracted_frame(), EXSPEC, models=MODELS)
    f = r.frame
    assert len(f) == 12
    assert set(f["extraction_method"]) == {"upstream_footprint_weighted", "source_region_mean"}
    fp = f[f["extraction_method"] == "upstream_footprint_weighted"]
    src = extracted_frame()
    assert np.allclose(np.sort(fp["et_mm"]), np.sort(src["ET_fp"]))  # values used as delivered
    assert set(f["fp_weight_retained_denominator"]) == {"normalized_truncated_footprint"}
    assert set(f["day_alignment"]) == {"unverified"}
    assert f["model_asset_id"].str.endswith("_abc12").all()
    assert r.report.ok and "model_asset_consistent" in r.report.codes()


def test_extracted_day_alignment_declared():
    products = {m: ProductSpec(m, units="mm/day", day_definition="local_standard_time",
                               temporal_support="interpolated_daily") for m in MODELS.models}
    stations = {"US-XXX": STATION}
    r = adapt_extracted_table(extracted_frame(), EXSPEC, models=MODELS, products=products,
                              stations=stations)
    assert set(r.frame["day_alignment"]) == {"declared"}
    with pytest.raises(ContractError, match="no product declaration"):
        adapt_extracted_table(extracted_frame(), EXSPEC, models=MODELS,
                              products={"ssebop": products["ssebop"]})


def test_extracted_filename_mismatch():
    df = extracted_frame()
    df.loc[0, "openet_file"] = df.loc[1, "openet_file"]  # wrong date
    df.loc[2, "footprint_file"] = "ffp_US-ZZZ_daily_etw_20250105"  # wrong site
    with pytest.raises(DataValidationError) as ei:
        adapt_extracted_table(df, EXSPEC, models=MODELS)
    codes = ei.value.report.codes("error")
    assert codes == {"model_asset_mismatch", "footprint_asset_mismatch"}


def test_extracted_rejects_duplicates_unknown_models_and_bad_fractions():
    df = extracted_frame()
    with pytest.raises(DuplicateKeyError):
        adapt_extracted_table(pd.concat([df, df.iloc[[0]]]), EXSPEC, models=MODELS)
    with pytest.raises(DataValidationError, match="unknown_model"):
        adapt_extracted_table(df.assign(model="metric"), EXSPEC, models=MODELS)
    with pytest.raises(DataValidationError, match="retained_fraction_out_of_range"):
        adapt_extracted_table(df.assign(valid_weight_fraction=1.2), EXSPEC, models=MODELS)
    with pytest.raises(DataValidationError, match="dates_with_time"):
        adapt_extracted_table(df.assign(footprint_date=df["footprint_date"] + pd.Timedelta("1h")),
                              EXSPEC, models=MODELS)


def test_extracted_unity_tolerance_is_a_warning():
    r = adapt_extracted_table(extracted_frame().assign(unity_check=0.9), EXSPEC, models=MODELS)
    assert "weight_sum_not_unity" in r.report.codes("warning")
    assert r.frame["fp_weight_sum"].eq(0.9).all()  # retained, not corrected


def test_extracted_null_values_kept():
    df = extracted_frame()
    df.loc[0, "ET_fp"] = np.nan
    r = adapt_extracted_table(df, EXSPEC, models=MODELS)
    assert r.frame["et_mm"].isna().sum() == 1
    assert "null_et" in r.report.codes("info")
