"""Shared pytest fixtures.

Planned contents:

* ``Base`` / declarative registry rebuilt per test module so mappers from one
  test never leak into another
* in-memory SQLite engine + session fixtures (sync and async)
* a ``sql_count`` / statement-capturing fixture built on the ``before_cursor_execute``
  event, used to assert that a loaded relationship emits **no** extra query
"""

from __future__ import annotations
