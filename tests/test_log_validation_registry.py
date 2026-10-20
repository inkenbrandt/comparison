import json
import logging

import pytest

from etcompare.errors import DataValidationError
from etcompare.log import JsonFormatter, get_logger, log_event
from etcompare.registry import ModelRegistry, SiteRegistry
from etcompare.validation import ValidationReport


def test_structured_log_record(caplog):
    logger = get_logger("tests")
    assert logger.name == "etcompare.tests"
    with caplog.at_level(logging.INFO, logger="etcompare"):
        log_event(logger, "thing_done", n_rows=3, path="x.csv")
    (rec,) = caplog.records
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["event"] == "thing_done" and payload["n_rows"] == 3


def test_report_raises_with_all_issues():
    r = ValidationReport("input")
    r.warning("w", "just a warning")
    assert r.ok
    r.error("e1", "bad", n_rows=2)
    r.error("e2", "worse")
    with pytest.raises(DataValidationError) as ei:
        r.raise_for_errors()
    assert ei.value.report is r
    assert "e1" in str(ei.value) and "e2" in str(ei.value)
    assert list(r.to_frame()["code"]) == ["w", "e1", "e2"]


def test_model_registry():
    reg = ModelRegistry.default()
    assert reg.resolve("ssebop") == "ssebop"
    assert reg.is_ensemble("ensemble") and not reg.is_ensemble("sims")
    with pytest.raises(KeyError):
        reg.resolve("SSEBop")
    reg2 = ModelRegistry(reg.models, aliases={"SSEBop": "ssebop"}, ensembles=reg.ensembles)
    assert reg2.resolve("SSEBop") == "ssebop"
    with pytest.raises(ValueError):
        ModelRegistry(reg.models, aliases={"x": "nope"})


def test_site_registry():
    reg = SiteRegistry(aliases={"US_UTM": "XX-UTM"})
    assert reg.resolve("XX-UTJ") == ("XX-UTJ", False)
    assert reg.resolve("US_UTM") == ("XX-UTM", True)
    with pytest.raises(KeyError):
        reg.resolve("USUTJ")
    with pytest.raises(ValueError):
        SiteRegistry(aliases={"a": "bad id"})
