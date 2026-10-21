"""Filename parsers for OpenET rasters and footprint rasters.

OpenET export names look like::

    XX-UTD_XX-UTG_XX-UTM_XX-UTW_ensemble_et_2021_01_01_9abcf.tif

The parser anchors on the ``_<model>_<variable>_YYYY_MM_DD_<suffix>.tif`` tail
using the model registry, then reads the prefix as an unordered set of site
tokens (decisions.md D-013). It never splits blindly on underscores, because
site aliases such as ``US_UTM`` contain one.

Processed copies may carry a declared processing tag after the API suffix, e.g.
``XX-UTJ_sims_et_2025_01_01_d04f3_buffer400m_clip.tif`` (tag ``buffer400m_clip``,
D-069). Only tags passed in ``processing_tags`` are accepted; any other text after
the suffix is still a parse error. The tag is recorded on the asset.

Footprint names look like ``ffp_XX-UTJ_daily_etw_20240605.tif`` or
``ffp_XX-UTJ_monthly_etw_202406.tif``. The layer token (``etw``, ``mean``) is
recorded as text; it does not establish a weighting method (D-009). The leading
token names the footprint model (``ffp``, ``ffp_xr``, ``km``, ``ls`` or ``wang``, e.g.
``km_XX-UTW_daily_etw_20210705.tif``). Several can share a folder, site, day and
layer, so a footprint set declares which one it holds.

A filename date is a label, never evidence of temporal support.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePath

from etcompare.config import FOOTPRINT_FILENAME_PREFIXES
from etcompare.errors import FilenameParseError
from etcompare.registry import ModelRegistry, SiteRegistry

DEFAULT_VARIABLES = ("et",)
DEFAULT_SUFFIX_PATTERN = r"[0-9a-f]{5}"
DEFAULT_EXTENSIONS = (".tif", ".tiff")

_GENERIC_OPENET = re.compile(
    r"^(?P<prefix>.+)_(?P<model>[^_]+)_(?P<variable>[^_]+)_(?P<y>\d{4})_(?P<m>\d{2})_"
    r"(?P<d>\d{2})_(?P<suffix>[^_.]+)(?P<ext>\.[^_]+)$"
)
_FOOTPRINT = re.compile(
    rf"^(?P<product>{'|'.join(sorted(FOOTPRINT_FILENAME_PREFIXES, key=len, reverse=True))})_"
    r"(?P<site>.+)_(?P<agg>daily|monthly)_(?P<layer>[A-Za-z0-9]+)_(?P<stamp>\d+)"
    r"(?P<ext>\.tiff?)?$"
)


@dataclass(frozen=True)
class OpenETAsset:
    """Identity parsed from one OpenET raster filename."""

    asset_id: str
    filename: str
    sites: tuple[str, ...]
    model: str
    model_token: str
    variable: str
    date: dt.date
    suffix: str
    site_aliases_applied: tuple[tuple[str, str], ...] = ()
    path: Path | None = None
    processing_tag: str | None = None


@dataclass(frozen=True)
class FootprintAsset:
    """Identity parsed from one footprint raster filename."""

    asset_id: str
    filename: str
    site_id: str
    period: str  # "day" or "month"
    layer_token: str
    date: dt.date  # period start
    site_alias_applied: tuple[str, str] | None = None
    path: Path | None = None
    product: str = "ffp"  # leading filename token, one of FOOTPRINT_FILENAME_PREFIXES


@lru_cache(maxsize=64)
def _openet_regex(
    tokens: tuple[str, ...], variables: tuple[str, ...], suffix: str, exts: tuple[str, ...],
    tags: tuple[str, ...] = (),
) -> re.Pattern[str]:
    alt = "|".join(re.escape(t) for t in tokens)
    var = "|".join(re.escape(v) for v in variables)
    ext = "|".join(re.escape(e) for e in exts)
    tag = rf"(?:_(?P<tag>{'|'.join(re.escape(t) for t in tags)}))?" if tags else ""
    return re.compile(
        rf"^(?P<prefix>.+)_(?P<model>{alt})_(?P<variable>{var})_(?P<y>\d{{4}})_(?P<m>\d{{2}})_"
        rf"(?P<d>\d{{2}})_(?P<suffix>{suffix}){tag}(?P<ext>{ext})$"
    )


def parse_site_prefix(
    prefix: str, sites: SiteRegistry, *, filename: str = ""
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Parse ``XX-UTJ_XX-CdM`` into sorted site IDs plus the aliases applied.

    Tokens are matched left to right: an explicit alias first (longest first),
    then the site pattern. Each token must end at ``_`` or the end of the
    prefix. Unrecognized text or a repeated site raises
    :class:`FilenameParseError`.
    """
    where = filename or prefix
    site_rx = re.compile(rf"(?:{sites.pattern})(?=_|$)")
    aliases = sorted(sites.aliases, key=len, reverse=True)
    found: list[str] = []
    applied: list[tuple[str, str]] = []
    pos = 0
    while True:
        for alias in aliases:
            end = pos + len(alias)
            if prefix.startswith(alias, pos) and (end == len(prefix) or prefix[end] == "_"):
                found.append(sites.aliases[alias])
                applied.append((alias, sites.aliases[alias]))
                pos = end
                break
        else:
            m = site_rx.match(prefix, pos)
            if not m:
                raise FilenameParseError(
                    f"{where}: unrecognized site token at {prefix[pos:]!r}; declare a site "
                    "alias if this is a site identifier"
                )
            found.append(m.group(0))
            pos = m.end()
        if pos == len(prefix):
            break
        pos += 1  # skip the separating underscore
        if pos == len(prefix):
            raise FilenameParseError(f"{where}: site list ends with '_'")
    dupes = sorted({s for s in found if found.count(s) > 1})
    if dupes:
        raise FilenameParseError(f"{where}: site(s) {dupes} listed more than once")
    return tuple(sorted(found)), tuple(applied)


def _date(y: str, m: str, d: str, where: str) -> dt.date:
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError as exc:
        raise FilenameParseError(f"{where}: invalid date {y}-{m}-{d} ({exc})") from exc


def parse_openet_filename(
    name: str | PurePath,
    *,
    models: ModelRegistry,
    sites: SiteRegistry | None = None,
    variables: tuple[str, ...] = DEFAULT_VARIABLES,
    suffix_pattern: str = DEFAULT_SUFFIX_PATTERN,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    processing_tags: tuple[str, ...] = (),
) -> OpenETAsset:
    """Parse an OpenET raster filename into sites, model, variable and date label.

    ``processing_tags`` lists the declared tags allowed after the API suffix.
    """
    path = Path(name) if isinstance(name, PurePath) else None
    filename = PurePath(name).name
    sites = sites or SiteRegistry()
    rx = _openet_regex(tuple(models.tokens()), tuple(variables), suffix_pattern,
                       tuple(extensions), tuple(processing_tags))
    m = rx.match(filename)
    if not m:
        raise FilenameParseError(_diagnose_openet(filename, models, variables, suffix_pattern,
                                                  extensions))
    site_ids, applied = parse_site_prefix(m["prefix"], sites, filename=filename)
    return OpenETAsset(
        asset_id=filename[: -len(m["ext"])],
        filename=filename,
        sites=site_ids,
        model=models.resolve(m["model"]),
        model_token=m["model"],
        variable=m["variable"],
        date=_date(m["y"], m["m"], m["d"], filename),
        suffix=m["suffix"],
        site_aliases_applied=applied,
        path=path,
        processing_tag=m.groupdict().get("tag"),
    )


def _diagnose_openet(
    filename: str, models: ModelRegistry, variables: tuple[str, ...], suffix_pattern: str,
    extensions: tuple[str, ...],
) -> str:
    expected = "<sites>_<model>_<variable>_YYYY_MM_DD_<suffix>.tif"
    g = _GENERIC_OPENET.match(filename)
    if not g:
        return f"{filename}: does not match {expected}"
    if g["ext"] not in extensions:
        return f"{filename}: extension {g['ext']!r} not in {list(extensions)}"
    try:
        models.resolve(g["model"])
    except KeyError:
        return (f"{filename}: model token {g['model']!r} is not registered; add it to the "
                "products or model_aliases configuration")
    if g["variable"] not in variables:
        return f"{filename}: variable {g['variable']!r} not in {list(variables)}"
    if not re.fullmatch(suffix_pattern, g["suffix"]):
        return f"{filename}: suffix {g['suffix']!r} does not match {suffix_pattern!r}"
    return f"{filename}: does not match {expected}"


def parse_footprint_filename(
    name: str | PurePath, *, sites: SiteRegistry | None = None
) -> FootprintAsset:
    """Parse ``<model>_<site>_<daily|monthly>_<layer>_<YYYYMMDD|YYYYMM>[.tif]``.

    ``<model>`` is one of :data:`~etcompare.config.FOOTPRINT_FILENAME_PREFIXES`.

    The extension is optional so that extension-less identifiers stored in
    tables (e.g. ``footprint_file``) parse the same way.
    """
    path = Path(name) if isinstance(name, PurePath) else None
    filename = PurePath(name).name
    m = _FOOTPRINT.match(filename)
    if not m:
        raise FilenameParseError(
            f"{filename}: does not match <model>_<site>_<daily|monthly>_<layer>_<date>.tif "
            f"(model: {'|'.join(FOOTPRINT_FILENAME_PREFIXES)})"
        )
    stamp = m["stamp"]
    if m["agg"] == "daily":
        if len(stamp) != 8:
            raise FilenameParseError(f"{filename}: daily footprint needs YYYYMMDD, got {stamp}")
        date, period = _date(stamp[:4], stamp[4:6], stamp[6:], filename), "day"
    else:
        if len(stamp) != 6:
            raise FilenameParseError(f"{filename}: monthly footprint needs YYYYMM, got {stamp}")
        date, period = _date(stamp[:4], stamp[4:], "01", filename), "month"
    site_ids, applied = parse_site_prefix(m["site"], sites or SiteRegistry(), filename=filename)
    if len(site_ids) != 1:
        raise FilenameParseError(f"{filename}: a footprint names exactly one site, got "
                                 f"{list(site_ids)}")
    stem = filename[: -len(m["ext"])] if m["ext"] else filename
    return FootprintAsset(
        asset_id=stem,
        filename=filename,
        site_id=site_ids[0],
        period=period,
        layer_token=m["layer"],
        date=date,
        site_alias_applied=applied[0] if applied else None,
        path=path,
        product=m["product"],
    )
