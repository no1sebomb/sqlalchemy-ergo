"""``CounterProperty`` / ``counter_property`` — count rows on the other side of a relationship.

Intent
------
Given a relationship, expose an attribute that answers "how many related rows
are there?" with the cheapest source available at access time:

* instance access, relationship already loaded -> ``len(obj.books)`` (no SQL)
* instance access, relationship not loaded -> scalar ``SELECT count(*)``
  subquery, emitted as a deferred ``column_property``
* class access (``Human.total_books``) -> the SQL expression itself, so it can
  be used in ``order_by``, ``filter``, ``having`` and friends

Sketch of the target usage::

    class Human(Base):
        books = relationship("Book")
        total_books = counter_property(books)

    session.scalars(select(Human).order_by(Human.total_books.desc()))

Notes for implementation
------------------------
* The underlying expression is a correlated ``select(func.count()).where(<join
  condition>)`` derived from the relationship's ``primaryjoin`` (plus
  ``secondaryjoin`` for many-to-many).
* The relationship may not be configured yet when the descriptor is created
  (class body evaluation order, string-based targets), so mapper configuration
  has to be deferred — build the expression lazily on first use / via a mapper
  configuration event.
* Loaded-state detection must not trigger a lazy load; inspect the instance
  state's ``unloaded`` set rather than touching the attribute.
"""

from __future__ import annotations
