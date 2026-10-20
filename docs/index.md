# etcompare

etcompare compares daily OpenET model evapotranspiration (ET) with quality-controlled eddy
covariance (EC) station ET. It builds on products you already have:

- daily station ET processed with [flux-data-qaqc](https://github.com/inkenbrandt/flux-data-qaqc);
- footprint rasters from [fluxfootprints](https://github.com/inkenbrandt/footprints);
- daily OpenET GeoTIFFs, or a table of values already extracted from them.

It does not redo EC processing, energy-balance correction, footprint modelling or
satellite ET retrieval.

Every analysis is declared in one YAML configuration and runs from the command line
(`etcompare run config.yaml`) or from Python. Each run writes:

- canonical Parquet tables, with CSV copies;
- a run manifest;
- a report that states which analyses were skipped and why.

```{admonition} Scope of results
:class: important
Results describe the stations and periods you supply. A summary across sites with one site
restates that site. Nothing in this package validates a model in general, and a synthetic
demonstration never describes observed performance.
```

```{toctree}
:maxdepth: 2
:caption: Using etcompare

getting_started
configuration
method_guide
limitations
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
data_dictionary
references
decisions
input_contract
changelog
```
