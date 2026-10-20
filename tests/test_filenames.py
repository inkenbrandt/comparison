import datetime as dt
from pathlib import Path

import pytest

from etcompare.adapters.filenames import (
    parse_footprint_filename,
    parse_openet_filename,
    parse_site_prefix,
)
from etcompare.errors import FilenameParseError
from etcompare.registry import ModelRegistry, SiteRegistry

MODELS = ModelRegistry.default()


def test_guide_example_multi_site():
    a = parse_openet_filename("XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf.tif",
                              models=MODELS)
    assert a.sites == ("XX-UTD", "XX-UTG", "XX-UTM", "XX-UTW")
    assert a.model == "ensemble" and a.variable == "et"
    assert a.date == dt.date(2021, 1, 1)
    assert a.asset_id == "XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf"
    assert a.path is None


def test_single_site_and_path_input():
    a = parse_openet_filename(Path("x") / "XX-UTE_sims_et_2025_09_12_d04f3.tif", models=MODELS)
    assert a.sites == ("XX-UTE",) and a.model == "sims" and a.path is not None


def test_site_order_is_a_set():
    a = parse_openet_filename("XX-UTJ_XX-CdM_ssebop_et_2024_06_05_779b6.tif", models=MODELS)
    b = parse_openet_filename("XX-CdM_XX-UTJ_ssebop_et_2024_06_05_779b6.tif", models=MODELS)
    assert a.sites == b.sites == ("XX-CdM", "XX-UTJ")


def test_model_alias():
    reg = ModelRegistry(MODELS.models, aliases={"pt-jpl": "ptjpl"}, ensembles=MODELS.ensembles)
    a = parse_openet_filename("XX-UTJ_pt-jpl_et_2025_01_03_abcde.tif", models=reg)
    assert a.model == "ptjpl" and a.model_token == "pt-jpl"


def test_site_alias_with_underscore():
    sites = SiteRegistry(aliases={"US_UTM": "XX-UTM"})
    a = parse_openet_filename("XX-UTD_US_UTM_eemetric_et_2021_01_01_9abcf.tif", models=MODELS,
                              sites=sites)
    assert a.sites == ("XX-UTD", "XX-UTM")
    assert a.site_aliases_applied == (("US_UTM", "XX-UTM"),)
    with pytest.raises(FilenameParseError, match="declare a site alias"):
        parse_openet_filename("XX-UTD_US_UTM_eemetric_et_2021_01_01_9abcf.tif", models=MODELS)


@pytest.mark.parametrize(
    "name, match",
    [
        ("XX-UTJ_ssebop_et_2021_02_30_9abcf.tif", "invalid date"),
        ("XX-UTJ_ssebop_et_2021_13_01_9abcf.tif", "invalid date"),
        ("XX-UTJ_ssebop_et_21_01_01_9abcf.tif", "does not match"),
        ("XX-UTJ_mystery_et_2021_01_01_9abcf.tif", "not registered"),
        ("XX-UTJ_ssebop_ndvi_2021_01_01_9abcf.tif", "variable"),
        ("XX-UTJ_ssebop_et_2021_01_01_XYZ.tif", "suffix"),
        ("XX-UTJ_ssebop_et_2021_01_01_9abcf.png", "extension"),
        ("XX-UTJ_XX-UTJ_ssebop_et_2021_01_01_9abcf.tif", "more than once"),
        ("ssebop_et_2021_01_01_9abcf.tif", "does not match"),
        ("UTJ_ssebop_et_2021_01_01_9abcf.tif", "unrecognized site"),
        ("XX-UTJ__ssebop_et_2021_01_01_9abcf.tif", "ends with"),
    ],
)
def test_rejects_malformed(name, match):
    with pytest.raises(FilenameParseError, match=match):
        parse_openet_filename(name, models=MODELS)


def test_prefix_parser_directly():
    assert parse_site_prefix("XX-UTX_XX-UTY_XX-UTH_XX-UTP", SiteRegistry())[0] == (
        "XX-UTH", "XX-UTP", "XX-UTX", "XX-UTY")


def test_footprint_names():
    d = parse_footprint_filename("ffp_XX-UTJ_daily_etw_20240605.tif")
    assert (d.site_id, d.period, d.layer_token, d.date) == ("XX-UTJ", "day", "etw",
                                                           dt.date(2024, 6, 5))
    assert d.asset_id == "ffp_XX-UTJ_daily_etw_20240605"
    m = parse_footprint_filename("ffp_XX-UTJ_monthly_mean_202406.tif")
    assert (m.period, m.date, m.layer_token) == ("month", dt.date(2024, 6, 1), "mean")
    bare = parse_footprint_filename("ffp_XX-UTJ_daily_etw_20240605")
    assert bare.asset_id == d.asset_id


@pytest.mark.parametrize("name", [
    "ffp_XX-UTJ_daily_etw_202406.tif", "ffp_XX-UTJ_monthly_etw_20240605.tif",
    "ffp_XX-UTJ_daily_etw_20240631.tif", "ffp_XX-UTJ_XX-CdM_daily_etw_20240605.tif",
    "fp_XX-UTJ_daily_etw_20240605.tif",
])
def test_footprint_rejects(name):
    with pytest.raises(FilenameParseError):
        parse_footprint_filename(name)
