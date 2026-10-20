"""Station reference variants (decisions.md D-004).

==========================  ===================  ===============================================
variant                     value column         rows
==========================  ===================  ===============================================
``et_raw``                  ``et_raw_mm``        raw ET present, provenance not inconsistent
``et_corrected_measured``   ``et_corrected_mm``  ``corrected_from_raw`` only
``et_corrected_inclusive``  ``et_corrected_mm``  ``corrected_from_raw`` and ``corrected_filled``
==========================  ===================  ===============================================

``ET_fill`` is never a reference. Rows outside a variant, or with a null
value, are not part of that variant; nothing is filled in.
"""

from __future__ import annotations

import pandas as pd

from etcompare.schemas import REFERENCE_VARIANTS, STATION_DAILY, CanonicalTable

_RULES = {
    "et_raw": ("et_raw_mm", None),
    "et_corrected_measured": ("et_corrected_mm", ("corrected_from_raw",)),
    "et_corrected_inclusive": ("et_corrected_mm", ("corrected_from_raw", "corrected_filled")),
}


def reference_values(station_daily: CanonicalTable | pd.DataFrame, variant: str
                     ) -> pd.DataFrame:
    """Rows of ``variant``: ``site_id``, ``date``, ``observed_mm``, ``reference_provenance``."""
    if variant not in REFERENCE_VARIANTS:
        raise ValueError(f"unknown reference variant {variant!r}; expected "
                         f"{list(REFERENCE_VARIANTS)}")
    frame = station_daily.frame if isinstance(station_daily, CanonicalTable) else station_daily
    missing = [c for c in STATION_DAILY.primary_key + ("et_raw_mm", "et_corrected_mm",
                                                        "reference_provenance")
               if c not in frame.columns]
    if missing:
        raise ValueError(f"station_daily lacks column(s) {missing}")
    col, provenances = _RULES[variant]
    prov = frame["reference_provenance"]
    keep = frame[col].notna()
    keep &= prov.isin(provenances) if provenances else prov != "inconsistent_flags"
    out = frame.loc[keep, ["site_id", "date", col, "reference_provenance"]]
    return out.rename(columns={col: "observed_mm"}).reset_index(drop=True)
