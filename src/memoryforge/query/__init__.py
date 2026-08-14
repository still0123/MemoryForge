"""Query APIs grouped under the :mod:`memoryforge.query` namespace.

The lazy attribute bridge keeps the pre-0.4 ``memoryforge.query`` module
imports working without eagerly importing the query implementation while
other submodules are being initialized.
"""

from importlib import import_module
from types import ModuleType
from typing import Any

_IMPLEMENTATION = "memoryforge.query.query"


def _implementation() -> ModuleType:
    return import_module(_IMPLEMENTATION)


def __getattr__(name: str) -> Any:
    return getattr(_implementation(), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation())))
