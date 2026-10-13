import datetime as dt

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from etcompare.errors import DuplicateKeyError, SchemaError
from etcompare.io.tables import read_metadata, read_table, write_table
from etcompare.results import ExclusionLog, format_key
from etcompare.schemas import (
    EXTRACTIONS,
    SCHEMAS,
    STATION_DAILY,
    CanonicalTable,
    check_primary_key,
    conform,
)


def synthetic(schema, n=3):
    """Rows covering every column; row 1 is null wherever the schema allows."""
    data = {}
    for c in schema.columns:
        vals = []
        for i in range(n):
            null = i == 1 and c.nullable
            if c.kind == "string":
                v = c.allowed[i % len(c.allowed)] if c.allowed else f"00{i}-{c.name}"
                if c.allowed and c.name in schema.primary_key:
                    v = c.allowed[0]
                vals.append(None if null else v)
            elif c.kind == "date":
                vals.append(pd.NaT if null else pd.Timestamp("2024-02-27") + pd.Timedelta(days=i))
            elif c.kind == "float":
                vals.append(np.nan if null else 0.1 * i - 0.05)
            elif c.kind == "int":
                vals.append(pd.NA if null else i)
            elif c.kind == "bool":
                vals.append(pd.NA if null else bool(i % 2))
        data[c.name] = pd.Series(vals, dtype=c.dtype)
    return pd.DataFrame(data)


def test_no_ambiguous_n_columns():
    for schema in SCHEMAS.values():
        assert "n" not in schema.names


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_parquet_round_trip_every_schema(tmp_path, name):
    schema = SCHEMAS[name]
    frame = synthetic(schema)
    table = CanonicalTable.build(frame, schema, {"source": "synthetic", "n_files": 2})
    path = write_table(table, tmp_path / f"{name}.parquet")
    back = read_table(path)
    assert back.schema is schema
    pd.testing.assert_frame_equal(back.frame, table.frame)
    assert dict(back.attributes) == {"source": "synthetic", "n_files": 2}
    meta = read_metadata(path)
    assert meta["units"] == schema.units
    assert meta["primary_key"] == list(schema.primary_key)
    arrow_schema = pq.read_schema(path)
    for c in schema.columns:
        if c.kind == "date":
            assert arrow_schema.field(c.name).type == pa.date32()
            assert meta["date_columns"][c.name] == "station_day_label"


def test_round_trip_preserves_nulls_identifiers_and_extras(tmp_path):
    frame = pd.DataFrame({
        "site_id": ["US-UTJ", "US-UTJ", "007"],
        "date": pd.to_datetime(["2024-06-05", "2024-06-06", "2024-06-05"]),
        "et_raw_mm": [1.5, np.nan, -0.2],
        "et_corrected_mm": [1.8, 2.0, np.nan],
        "gap_filled": [False, True, None],
        "raw_available": [True, False, True],
        "reference_provenance": ["corrected_from_raw", "corrected_filled", "inconsistent_flags"],
        "src_ET_gap": [False, True, False],
        "src_note": ["a", None, "0012"],
        "src_count": pd.array([1, None, 3], dtype="Int64"),
    })
    table = CanonicalTable.build(frame, STATION_DAILY)
    back = read_table(write_table(table, tmp_path / "s.parquet"))
    pd.testing.assert_frame_equal(back.frame, table.frame)
    f = back.frame
    assert f.loc[2, "site_id"] == "007"
    assert pd.isna(f.loc[1, "et_raw_mm"]) and pd.isna(f.loc[2, "gap_filled"])
    assert f.loc[2, "et_raw_mm"] == -0.2  # negative ET is never clipped
    assert f["date"].dtype == "datetime64[ns]"
    assert f.loc[2, "src_note"] == "0012" and pd.isna(f.loc[1, "src_count"])
    assert "closure_factor" in f.columns and f["closure_factor"].isna().all()


def test_read_with_wrong_schema(tmp_path):
    t = CanonicalTable.build(synthetic(STATION_DAILY), STATION_DAILY)
    p = write_table(t, tmp_path / "s.parquet")
    with pytest.raises(SchemaError, match="expected 'extractions'"):
        read_table(p, EXTRACTIONS)


def test_plain_parquet_needs_schema(tmp_path):
    p = tmp_path / "plain.parquet"
    synthetic(STATION_DAILY).to_parquet(p)
    with pytest.raises(SchemaError, match="pass schema="):
        read_table(p)
    assert len(read_table(p, STATION_DAILY)) == 3


def base_daily():
    return pd.DataFrame({
        "site_id": ["A", "A"], "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "et_raw_mm": [1.0, 2.0], "et_corrected_mm": [1.1, 2.2], "gap_filled": [False, False],
        "raw_available": [True, True],
        "reference_provenance": ["corrected_from_raw", "corrected_from_raw"],
    })


def test_duplicate_and_null_keys_detected_before_joins():
    df = base_daily()
    df.loc[1, "date"] = df.loc[0, "date"]
    with pytest.raises(DuplicateKeyError, match="duplicated key"):
        conform(df, STATION_DAILY)
    with pytest.raises(DuplicateKeyError, match="null key"):
        check_primary_key(pd.DataFrame({"k": ["a", None]}), ["k"], table="t")


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d.drop(columns="raw_available"), "missing required"),
        (lambda d: d.assign(date=d["date"].dt.tz_localize("UTC")), "timezone-aware"),
        (lambda d: d.assign(date=d["date"] + pd.Timedelta(hours=7)), "time of day"),
        (lambda d: d.assign(date=["2024/01/01", "2024/01/02"]), "ISO"),
        (lambda d: d.assign(et_raw_mm=["1", "2"]), "expected numbers"),
        (lambda d: d.assign(gap_filled=["False", "True"]), "boolean"),
        (lambda d: d.assign(site_id=[1, 2]), "expected strings"),
        (lambda d: d.assign(reference_provenance=["measured", "corrected_from_raw"]),
         "not in"),
        (lambda d: d.assign(raw_available=[True, None]), "non-nullable"),
    ],
)
def test_conform_rejects(mutate, match):
    with pytest.raises(SchemaError, match=match):
        conform(mutate(base_daily()), STATION_DAILY)


def test_conform_accepts_date_objects_and_iso_strings():
    d = base_daily()
    a = conform(d.assign(date=[dt.date(2024, 1, 1), dt.date(2024, 1, 2)]), STATION_DAILY)
    b = conform(d.assign(date=["2024-01-01", "2024-01-02"]), STATION_DAILY)
    pd.testing.assert_frame_equal(a, b)


def test_extra_columns_rejected_when_not_allowed():
    df = synthetic(EXTRACTIONS)
    with pytest.raises(SchemaError, match="unexpected column"):
        conform(df.assign(surprise=1), EXTRACTIONS)


def test_exclusion_log():
    log = ExclusionLog()
    assert len(log.to_frame()) == 0
    key = format_key(site_id="US-UTJ", date=pd.Timestamp("2024-06-05"), model="sims")
    assert key == "site_id=US-UTJ|date=2024-06-05|model=sims"
    log.add("cohort", key, "reference_missing", severity="info")
    log.add_many("discovery", ["a.tif", "b.tif"], "unparseable_filename")
    df = log.to_frame()
    assert list(df["severity"]) == ["info", "warning", "warning"]
    assert log.counts()[("discovery", "unparseable_filename")] == 2
    with pytest.raises(SchemaError):
        bad = ExclusionLog()
        bad.add("s", "k", "r", severity="fatal")  # type: ignore[arg-type]
        bad.to_frame()
