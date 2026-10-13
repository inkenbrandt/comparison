"""etcompare: compare OpenET model estimates against eddy covariance station ET.

Importing the package performs no network access, opens no raster and does not
import optional (geospatial, plotting) dependencies. Functionality lives in
submodules:

- :mod:`etcompare.config` -- station, product and input declarations
- :mod:`etcompare.schemas` -- canonical table schemas and conformance checks
- :mod:`etcompare.results` -- exclusion log and adapter result objects
- :mod:`etcompare.validation` -- validation reports
- :mod:`etcompare.registry` -- model and site alias registries
- :mod:`etcompare.io` -- table reading, Parquet round trips, file discovery
- :mod:`etcompare.adapters` -- upstream product mappings
"""

from etcompare.errors import (
    ContractError,
    DataValidationError,
    DuplicateAssetError,
    DuplicateKeyError,
    EtcompareError,
    FilenameParseError,
    OptionalDependencyError,
    SchemaError,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "ContractError",
    "DataValidationError",
    "DuplicateAssetError",
    "DuplicateKeyError",
    "EtcompareError",
    "FilenameParseError",
    "OptionalDependencyError",
    "SchemaError",
    "__version__",
]
