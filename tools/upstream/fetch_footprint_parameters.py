"""Read-only export of a station's footprint height parameters from the UGS database.

Runs with the footprints repository's environment (sqlalchemy + psycopg2), using the
user's own connection helper, exactly as footprint_workflow_v2-1.ipynb does:

  - groundwater.daily_footprint_parameters  (zone_status = 'primary')
  - groundwater.dynamic_station_data         (variable_name = 'irgason')
  - groundwater.station_attribute_view       (latitude, longitude)

Only SELECT statements are issued. The output is a local, git-ignored input file.

    ../footprints/.venv/Scripts/python.exe tools/upstream/fetch_footprint_parameters.py \
        US-UTJ outputs/footprint_inputs
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import text

DBCONNECT = "M:/My Drive/Python/dbconnect.py"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("station")
    ap.add_argument("out_dir")
    ap.add_argument("--dbconnect", default=DBCONNECT)
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location("dbconnect", args.dbconnect)
    dbconnect = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(dbconnect)  # type: ignore[union-attr]
    engine = dbconnect.postconn_ugs_write()

    with engine.connect() as con:
        con.execute(text("SET TRANSACTION READ ONLY"))
        params = pd.read_sql(text(
            "SELECT * FROM groundwater.daily_footprint_parameters "
            "WHERE stationid = :sid AND zone_status = 'primary' ORDER BY height_date"),
            con, params={"sid": args.station})
        irga = pd.read_sql(text(
            "SELECT variable_value FROM groundwater.dynamic_station_data "
            "WHERE variable_name = 'irgason' AND stationid = :sid"),
            con, params={"sid": args.station})
        loc = pd.read_sql(text(
            "SELECT stationid, latitude, longitude FROM groundwater.station_attribute_view "
            "WHERE stationid = :sid"), con, params={"sid": args.station})
    engine.dispose()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    params.to_parquet(out / f"{args.station}_daily_footprint_parameters.parquet")
    meta = {"station": args.station, "n_parameter_rows": len(params),
            "parameter_columns": list(params.columns),
            "irgason_height": irga["variable_value"].tolist(),
            "station_location": loc.astype(str).to_dict("records"),
            "queried": pd.Timestamp.now().isoformat(timespec="seconds")}
    (out / f"{args.station}_footprint_parameters.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(json.dumps(meta, indent=2, default=str))
    if len(params):
        print(params.describe(include="all").T.to_string())


if __name__ == "__main__":
    main()
