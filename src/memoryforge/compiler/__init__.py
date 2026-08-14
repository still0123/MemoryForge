"""Compiler APIs grouped under the :mod:`memoryforge.compiler` namespace.

The implementation module is loaded lazily so storage modules can import
individual compiler components without creating an initialization cycle.
"""

from importlib import import_module
from types import ModuleType
from typing import Any

_IMPLEMENTATION = "memoryforge.compiler.compiler"


def _implementation() -> ModuleType:
    return import_module(_IMPLEMENTATION)


def __getattr__(name: str) -> Any:
    return getattr(_implementation(), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation())))
