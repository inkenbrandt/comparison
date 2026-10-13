"""Profile the example station CSV and extracted-OpenET Parquet inputs.

Prints the facts recorded in docs/input_contract.md so they can be regenerated
when the inputs change. Read-only; writes nothing.

    uv run --with pandas --with pyarrow python tools/profile_inputs.py \
        US-UTJ_daily.csv station_oet_US-UTJ.parquet
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

RTOL, ATOL = 1e-9, 1e-9


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a: pd.Series, b: pd.Series) -> pd.Series:
    return pd.Series(np.isclose(a, b, rtol=RTOL, atol=ATOL), index=a.index)


def profile_station(path: Path) -> pd.DataFrame:
    print(f"## {path.name}\nsha256 {sha256(path)}")
    st = pd.read_csv(path)
    print(f"shape {st.shape}")
    d = pd.to_datetime(st["date"], format="%Y-%m-%d")
    full = pd.date_range(d.min(), d.max(), freq="D")
    print(f"dates {d.min().date()}..{d.max().date()} n={d.nunique()} "
          f"dupes={d.duplicated().sum()} calendar_complete={len(full) == len(d)}")
    print(f"ET_gap dtype={st['ET_gap'].dtype} values={st['ET_gap'].value_counts().to_dict()}")
    print("non-null counts:")
    print(st.notna().sum().to_string())

    raw, cor, gap = st["ET"].notna(), st["ET_corr"].notna(), st["ET_gap"].astype(bool)
    combo = pd.DataFrame({"raw": raw, "corr": cor, "gap": gap}).value_counts()
    print("raw/corr/gap combinations:\n" + combo.to_string())

    both = raw & cor
    print(f"ET_corr == ET*ebc_cf on raw days: "
          f"{close(st.loc[both, 'ET_corr'], st.loc[both, 'ET'] * st.loc[both, 'ebc_cf']).sum()}/{both.sum()}")
    print(f"ebc_cf null on raw days: {st.loc[raw, 'ebc_cf'].isna().sum()}")
    g = gap & cor
    print(f"ET_corr == ET_fill_val on gap days: "
          f"{close(st.loc[g, 'ET_corr'], st.loc[g, 'ET_fill_val']).sum()}/{g.sum()}")
    fill = st["ET_fill"].notna() & st["gridMET_ETr"].notna() & st["ETrF_filtered"].notna()
    print(f"ET_fill == gridMET_ETr*ETrF_filtered: "
          f"{close(st.loc[fill, 'ET_fill'], st.loc[fill, 'gridMET_ETr'] * st.loc[fill, 'ETrF_filtered']).sum()}/{fill.sum()}")
    r = raw & st["gridMET_ETr"].notna() & st["ETrF"].notna()
    for ref in ("ET", "ET_corr"):
        ok = close(st.loc[r, "ETrF"], st.loc[r, ref] / st.loc[r, "gridMET_ETr"]).sum()
        print(f"ETrF == {ref}/gridMET_ETr on raw days: {ok}/{r.sum()}")
    ebc = st["ebc_cf"].notna() & st["ebr_corr"].notna()
    print(f"ebc_cf == 1/ebr_corr: "
          f"{close(st.loc[ebc, 'ebc_cf'], 1 / st.loc[ebc, 'ebr_corr']).sum()}/{ebc.sum()}")
    print(f"ebc_cf range {st['ebc_cf'].min():.4f}..{st['ebc_cf'].max():.4f}")
    le = raw & st["le_1_1_1"].notna()
    for div in (2.45e6 / 86400, 28.4):
        ok = np.isclose(st.loc[le, "ET"], st.loc[le, "le_1_1_1"] / div, rtol=1e-3).sum()
        print(f"ET ~= le_1_1_1/{div:.4f} (rtol 1e-3): {ok}/{le.sum()}")
    ratio = (st.loc[le, "le_1_1_1"] / st.loc[le, "ET"])
    print(f"le_1_1_1/ET ratio: min {ratio.min():.4f} median {ratio.median():.4f} max {ratio.max():.4f}")
    for c in ("ET", "ET_corr", "ET_fill", "ET_fill_val", "h_1_1_1", "rh", "vpd", "ta_final",
              "ws_1_1_1", "p_1_1_1", "gridMET_prcp", "gridMET_ETo", "gridMET_ETr"):
        s = st[c]
        print(f"{c}: n={s.notna().sum()} min={s.min():.4g} max={s.max():.4g} "
              f"n<=0={(s <= 0).sum()} n<0={(s < 0).sum()}")
    ym = d.dt.to_period("M")
    month = pd.DataFrame({"days": ym.value_counts().sort_index(),
                          "raw": raw.groupby(ym).sum(), "corr": cor.groupby(ym).sum(),
                          "gap": gap.groupby(ym).sum()})
    print("by month:\n" + month.to_string())
    st["date"] = d
    return st


def profile_openet(path: Path, st: pd.DataFrame) -> None:
    print(f"\n## {path.name}\nsha256 {sha256(path)}")
    meta = pq.read_metadata(path)
    print(f"parquet created_by={meta.created_by!r} rows={meta.num_rows}")
    schema = pq.read_schema(path)
    print(f"schema metadata keys {list((schema.metadata or {}).keys())}")
    print(schema.to_string(show_schema_metadata=False))
    oe = pd.read_parquet(path)
    print(f"shape {oe.shape}; nulls {oe.isna().sum().sum()}")
    print(f"station_id {oe['station_id'].unique().tolist()}")
    key = ["station_id", "model", "footprint_date"]
    print(f"duplicate keys {oe.duplicated(key).sum()}")
    print(f"footprint_date tz {oe['footprint_date'].dt.tz}; "
          f"non-midnight {(oe['footprint_date'] != oe['footprint_date'].dt.normalize()).sum()}")

    # Filename-encoded date and site list versus the footprint_date column.
    m = oe["openet_file"].str.extract(r"^(?P<sites>.+?)_(?P<model>[a-z]+)_et_(?P<d>\d{4}_\d{2}_\d{2})_(?P<hash>[0-9a-f]+)\.tif$")
    print(f"openet_file parse failures {m['d'].isna().sum()}")
    fdate = pd.to_datetime(m["d"], format="%Y_%m_%d")
    print(f"openet_file date == footprint_date: {(fdate == oe['footprint_date']).sum()}/{len(oe)}")
    print(f"openet_file model == model: {(m['model'] == oe['model']).sum()}/{len(oe)}")
    print(f"openet_file site groups {m['sites'].value_counts().to_dict()}")
    print(f"hash suffixes distinct {m['hash'].nunique()}")
    fpd = pd.to_datetime(oe["footprint_file"].str.extract(r"_(\d{8})$")[0], format="%Y%m%d")
    print(f"footprint_file date == footprint_date: {(fpd == oe['footprint_date']).sum()}/{len(oe)}")
    print(f"footprint_file stems: {oe['footprint_file'].str.replace(r'_\d{8}$', '', regex=True).unique().tolist()}")
    print(f"openet_path prefixes: {oe['openet_path'].str.extract(r'^(/[^/]+/[^/]+/[^/]+/[^/]+)')[0].unique().tolist()}")
    print(f"openet_path endswith openet_file: {(oe.apply(lambda r: r['openet_path'].endswith('/' + r['openet_file']), axis=1)).sum()}/{len(oe)}")
    print(oe[["ET_fp", "ET_mean", "unity_check", "valid_weight_fraction"]].describe().to_string())
    print(f"unity_check |x-1| max {abs(oe['unity_check'] - 1).max():.3e}")
    print(f"valid_weight_fraction <0.90: {(oe['valid_weight_fraction'] < 0.9).sum()}; "
          f"==1 (1e-12): {np.isclose(oe['valid_weight_fraction'], 1, atol=1e-12, rtol=0).sum()}; "
          f">1+1e-8: {(oe['valid_weight_fraction'] > 1 + 1e-8).sum()}")
    low = oe["valid_weight_fraction"] < 0.9
    print(f"low-support rows by model: {oe.loc[low, 'model'].value_counts().to_dict()}")
    print(f"low-support distinct dates: {oe.loc[low, 'footprint_date'].nunique()}")

    cov = oe.groupby("model")["footprint_date"].agg(["count", "min", "max", "nunique"])
    print(cov.to_string())
    dates_by_model = oe.groupby("model")["footprint_date"].apply(set)
    common = set.intersection(*dates_by_model)
    print(f"dates common to all {len(dates_by_model)} products: {len(common)}")
    s = st.set_index("date")
    c = s.loc[sorted(common)]
    print(f"  of which raw ET present {c['ET'].notna().sum()}, ET_corr present "
          f"{c['ET_corr'].notna().sum()}, gap-filled {c['ET_gap'].astype(bool).sum()}")
    print(f"station dates without any OpenET row: {len(set(s.index) - set(oe['footprint_date']))}")
    print(f"OpenET dates absent from station table: {len(set(oe['footprint_date']) - set(s.index))}")
    j = oe.merge(st[["date", "ET", "ET_corr", "ET_gap"]], left_on="footprint_date",
                 right_on="date", how="left", validate="many_to_one")
    per = j.groupby("model").agg(raw=("ET", "count"), corr=("ET_corr", "count"),
                                 gap=("ET_gap", "sum"))
    print(per.to_string())


def main(argv: list[str]) -> None:
    st = profile_station(Path(argv[1]))
    profile_openet(Path(argv[2]), st)


if __name__ == "__main__":
    main(sys.argv)
