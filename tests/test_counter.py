"""Tests for ``counter_property``.

Cases to cover:

* unloaded relationship -> exactly one COUNT query, correct value
* loaded relationship (``selectinload``/``joinedload``) -> value from
  ``len()``, zero additional queries
* class-level access usable in ``order_by`` / ``where`` / ``having``
* one-to-many, many-to-many (``secondary``), and self-referential relationships
* empty collection -> ``0``, never ``None``
* pending / transient instance (not yet flushed) behaviour
* inheritance: subclass reuses the parent's counter
* async session path
"""

from __future__ import annotations
