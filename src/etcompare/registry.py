"""Model and site identifier registries.

Filenames and tables name models and sites with tokens. A registry maps every
accepted token to one canonical identifier, so parsing never guesses. Aliases
are explicit and the applied mapping is reported.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from etcompare.config import ProductSpec

#: The seven OpenET products (six models and the official ensemble). ``ensemble`` is the official
#: OpenET ensemble: a comparison product, not an independent member model.
OPENET_MODELS: Final = ("disalexi", "eemetric", "ensemble", "geesebal", "ptjpl", "sims", "ssebop")
OPENET_ENSEMBLE: Final = "ensemble"

#: AmeriFlux-style site identifier, e.g. ``XX-UTJ``.
DEFAULT_SITE_PATTERN: Final = r"[A-Z]{2}-[A-Za-z0-9]{3}"


@dataclass(frozen=True)
class ModelRegistry:
    """Canonical model names, their accepted tokens, and which are ensembles."""

    models: frozenset[str]
    aliases: Mapping[str, str] = field(default_factory=dict)
    ensembles: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))
        for alias, target in self.aliases.items():
            if target not in self.models:
                raise ValueError(f"model alias {alias!r} targets unregistered model {target!r}")
            if alias in self.models and alias != target:
                raise ValueError(f"model alias {alias!r} shadows a registered model")
        if not self.ensembles <= self.models:
            raise ValueError(f"ensembles {set(self.ensembles - self.models)} are not registered")

    @classmethod
    def default(cls) -> ModelRegistry:
        """The seven OpenET products: six models and the official ensemble."""
        return cls(frozenset(OPENET_MODELS), ensembles=frozenset({OPENET_ENSEMBLE}))

    @classmethod
    def from_products(
        cls, products: Mapping[str, ProductSpec], aliases: Mapping[str, str] | None = None
    ) -> ModelRegistry:
        return cls(
            frozenset(products),
            aliases=dict(aliases or {}),
            ensembles=frozenset(m for m, p in products.items() if p.is_ensemble),
        )

    def tokens(self) -> list[str]:
        """Every accepted token, longest first (for regex alternation)."""
        return sorted(set(self.models) | set(self.aliases), key=lambda t: (-len(t), t))

    def resolve(self, token: str) -> str:
        """Canonical model for ``token``; raise ``KeyError`` if unregistered."""
        if token in self.models:
            return token
        if token in self.aliases:
            return self.aliases[token]
        raise KeyError(token)

    def is_ensemble(self, model: str) -> bool:
        return self.resolve(model) in self.ensembles


@dataclass(frozen=True)
class SiteRegistry:
    """Site-token rules: a pattern for canonical IDs plus explicit aliases.

    ``aliases`` maps a source token (e.g. ``US_UTM``) to a canonical site ID
    (``XX-UTM``). Source files are never renamed.
    """

    aliases: Mapping[str, str] = field(default_factory=dict)
    pattern: str = DEFAULT_SITE_PATTERN

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))
        rx = re.compile(f"^(?:{self.pattern})$")
        for alias, target in self.aliases.items():
            if not rx.match(target):
                raise ValueError(f"site alias {alias!r} targets {target!r}, which does not "
                                 f"match the site pattern {self.pattern!r}")

    def resolve(self, token: str) -> tuple[str, bool]:
        """Return ``(site_id, alias_applied)``; raise ``KeyError`` if not a site token."""
        if token in self.aliases:
            return self.aliases[token], True
        if re.fullmatch(self.pattern, token):
            return token, False
        raise KeyError(token)

    def resolve_many(self, tokens: Iterable[str]) -> dict[str, str]:
        """Map each distinct token to its site ID (raises ``KeyError`` on the first bad one)."""
        return {t: self.resolve(t)[0] for t in set(tokens)}
