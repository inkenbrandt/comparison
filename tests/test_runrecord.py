"""Step 17: run manifest, identities, cache stamps and the command-line interface."""

import json
import os
import subprocess
import sys

import pytest

from etcompare.errors import SchemaError
from etcompare.io.tables import read_table, write_table
from etcompare.provenance import (
    PRESENTATION_MODULES,
    canonical_hash,
    code_identity,
    file_identity,
    git_state,
)
from etcompare.runrecord import (
    MANIFEST_VERSION,
    Cache,
    RunManifest,
    UnitRecord,
    new_run_id,
    status_table,
)


def manifest(**over):
    kw = dict(run_id="20260101T000000Z_abcdef12", command="etcompare run c.yaml",
              stages=["validate"], created_utc="2026-01-01T00:00:00+00:00",
              config_path="c.yaml", config_sha256="0" * 64, config_hash="a" * 64,
              code_identity="b" * 64, etcompare_version="0.1", git={"available": False},
              package_versions={"numpy": "2.0"}, upstream_declared=[],
              upstream_detected=[{"name": "fluxfootprints", "installed": False}],
              cache={"enabled": True, "raster_identity": "stat"},
              seeds=[{"unit": "bootstrap:c", "seed": 1}],
              exclusion_counts=[{"stage": "cohort", "reason_code": "x", "n": 3}],
              analyses=[{"unit_id": "validate", "status": "completed"}])
    kw.update(over)
    return RunManifest(**kw)


def test_manifest_round_trip_and_versioning(tmp_path):
    m = manifest()
    path = m.write(tmp_path)
    assert path == tmp_path / "runs" / m.run_id / "run_manifest.json"
    back = RunManifest.read(path)
    assert back == m and back.manifest_version == MANIFEST_VERSION
    assert RunManifest.read(tmp_path / "run_manifest.json") == m       # latest copy
    m2 = manifest(run_id="20260102T000000Z_abcdef12")
    m2.write(tmp_path)
    assert RunManifest.read(path) == m                                  # never overwritten
    assert RunManifest.read(tmp_path / "run_manifest.json").run_id == m2.run_id


def test_manifest_rejects_other_versions_and_unknown_keys():
    d = manifest().to_dict()
    with pytest.raises(SchemaError, match="version"):
        RunManifest.from_dict({**d, "manifest_version": "0"})
    with pytest.raises(SchemaError, match="unexpected"):
        RunManifest.from_dict({**d, "surprise": 1})
    with pytest.raises(SchemaError, match="missing"):
        RunManifest.from_dict({k: v for k, v in d.items() if k != "config_hash"})


def test_run_id_carries_the_config_hash():
    assert new_run_id("deadbeef" * 8).endswith("_deadbeef")


def test_file_identity(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("x", encoding="utf-8")
    sha = file_identity(p, "sha256")
    assert sha["sha256"] == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    st = file_identity(p, "stat")
    assert st["size"] == 1 and "sha256" not in st
    assert file_identity(tmp_path / "absent", "sha256") == {
        "path": str(tmp_path / "absent"), "exists": False}
    with pytest.raises(ValueError):
        file_identity(p, "md5")


def test_canonical_hash_ignores_key_order():
    assert canonical_hash({"a": 1, "b": [1, 2]}) == canonical_hash({"b": [1, 2], "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_code_identity_can_leave_out_presentation_modules():
    assert code_identity() != code_identity(PRESENTATION_MODULES)
    assert len(code_identity()) == 64


def test_git_state_outside_a_checkout(tmp_path):
    assert git_state(tmp_path) == {"available": False}


def test_status_table_round_trip(tmp_path):
    recs = [UnitRecord("analyze", "cohorts:c", "cohorts", "completed", elapsed_s=1.0),
            UnitRecord("analyze", "representativeness", "representativeness", "skipped",
                       "not_declared", "no representativeness declared")]
    t = status_table(recs)
    back = read_table(write_table(t, tmp_path / "s.parquet"))
    assert back.frame.equals(t.frame)
    with pytest.raises(ValueError):
        UnitRecord("analyze", "x", "x", "done")


def test_cache_stamp_validity(tmp_path):
    out = tmp_path / "o.csv"
    out.write_text("1", encoding="utf-8")
    cache = Cache(tmp_path)
    cache.write(UnitRecord("analyze", "metrics:c__m", "metrics", "completed", cache_key="k",
                           outputs=[str(out)]))
    assert cache.valid("analyze", "metrics:c__m", "k") is not None
    assert cache.valid("analyze", "metrics:c__m", "other") is None
    st = out.stat()
    os.utime(out, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert cache.valid("analyze", "metrics:c__m", "k") is None          # output modified
    off = Cache(tmp_path, enabled=False)
    assert off.valid("analyze", "metrics:c__m", "k") is None
    assert [r.unit_id for r in off.records()] == ["metrics:c__m"]


def test_failed_stamps_are_never_reused(tmp_path):
    cache = Cache(tmp_path)
    cache.write(UnitRecord("analyze", "u", "metrics", "failed", cache_key="k"))
    assert cache.valid("analyze", "u", "k") is None


# --------------------------------------------------------------------------- CLI

def cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "etcompare", *args], capture_output=True,
                          text=True, cwd=cwd)


def test_cli_help_lists_every_subcommand():
    p = cli("--help")
    assert p.returncode == 0
    for name in ("validate", "index", "extract", "analyze", "report", "run"):
        assert name in p.stdout


def test_cli_configuration_error_exits_2(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("stations: {}\nsurprise: 1\noutput_dir: out\n", encoding="utf-8")
    p = cli("validate", str(bad), "--log-format", "text")
    assert p.returncode == 2
    assert "configuration error" in p.stderr and "surprise" in p.stderr
    missing = cli("validate", str(tmp_path / "absent.yaml"))
    assert missing.returncode == 2


def test_cli_validate_writes_manifest_and_json_log(tmp_path):
    from etcompare.demo import write_demo

    cfg = write_demo(tmp_path)
    p = cli("validate", str(cfg))
    assert p.returncode == 0, p.stderr
    runs = list((tmp_path / "outputs" / "runs").iterdir())
    assert len(runs) == 1
    m = RunManifest.read(runs[0] / "run_manifest.json")
    assert m.status == "completed" and m.stages == ["validate"]
    assert m.config_sha256 and len(m.config_hash) == 64
    assert any(i["path"].endswith("XX-SY1_daily.csv") and "sha256" in i for i in m.inputs)
    assert m.upstream_declared[0]["name"] == "etcompare.demo"
    lines = (runs[0] / "log.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(x)["event"] for x in lines]
    assert events[0] == "run_started" and "unit_finished" in events
    first = p.stderr.splitlines()[0]
    assert json.loads(first)["event"] == "run_started"                  # structured stderr
