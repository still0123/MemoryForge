"""Evaluation APIs grouped under the :mod:`memoryforge.evaluation` namespace.

The implementation module is loaded on demand to preserve the lightweight
package import behavior of the pre-refactor module.
"""

from importlib import import_module
from types import ModuleType
from typing import Any

_IMPLEMENTATION = "memoryforge.evaluation.evaluation"


def _implementation() -> ModuleType:
    return import_module(_IMPLEMENTATION)


def __getattr__(name: str) -> Any:
    return getattr(_implementation(), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation())))
