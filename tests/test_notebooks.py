"""The committed notebooks: outputs stripped, no private paths, and their code runs
(on the synthetic demonstration; install magics are skipped)."""

import json
import os
from pathlib import Path

import pytest

NOTEBOOKS = sorted((Path(__file__).resolve().parents[1] / "notebooks").glob("*.ipynb"))


def test_three_notebooks_exist():
    assert [p.name for p in NOTEBOOKS] == ["01_colab_quickstart.ipynb",
                                           "02_pre_extracted_tables.ipynb",
                                           "03_raster_workflow.ipynb"]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_outputs_stripped_and_no_private_paths(path):
    nb = json.loads(path.read_text(encoding="utf-8"))
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            assert c["outputs"] == [] and c["execution_count"] is None
    text = path.read_text(encoding="utf-8")
    # Built from parts so that this file does not itself contain the strings it forbids.
    for private in ("US" + "-UT", "J" + ":/", "M" + ":/", "C:/" + "Users", "Hut" + "to",
                    "My " + "Drive/OpenET"):
        assert private not in text, private
    if path.name.startswith("01"):
        mount = [c for c in nb["cells"] if "drive.mount" in "".join(c["source"])]
        # The Drive mount exists, but only as a cell the user runs (commented out).
        assert mount and all(line.lstrip().startswith("#")
                             for line in "".join(mount[0]["source"]).splitlines())


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_code_runs(path, tmp_path, monkeypatch):
    if path.name.startswith(("01", "03")):
        pytest.importorskip("rasterio")
    pytest.importorskip("matplotlib")
    nb = json.loads(path.read_text(encoding="utf-8"))
    monkeypatch.chdir(tmp_path)
    ns: dict = {"__name__": "__main__"}
    for c in nb["cells"]:
        if c["cell_type"] != "code":
            continue
        src = "\n".join(line for line in "".join(c["source"]).splitlines()
                        if not line.lstrip().startswith(("%", "!")))
        exec(compile(src, f"{path.name}", "exec"), ns)  # noqa: S102 - our own notebook
    assert os.listdir(tmp_path)
