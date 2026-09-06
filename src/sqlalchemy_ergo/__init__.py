"""sqlalchemy-ergo: ergonomic helpers for SQLAlchemy ORM models.

Public API is re-exported here; internal modules are free to move between
releases. Anything not listed in ``__all__`` is not part of the contract.
"""

from __future__ import annotations

from sqlalchemy_ergo.counter import (
    CounterMismatchError,
    CounterProperty,
    counter_property,
)

__version__ = "0.1.0"

__all__ = [
    "CounterMismatchError",
    "CounterProperty",
    "__version__",
    "counter_property",
]
