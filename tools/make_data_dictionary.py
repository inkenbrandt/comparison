"""Write docs/data_dictionary.md from etcompare.schemas (tests check it is current)."""

from pathlib import Path

from etcompare.io.atomic import write_text_atomic
from etcompare.schemas import data_dictionary

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "docs" / "data_dictionary.md"
    write_text_atomic(data_dictionary(), path)
    print(f"wrote {path}")
