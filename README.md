# sqlalchemy-ergo

[![CI](https://github.com/no1sebomb/sqlalchemy-ergo/actions/workflows/ci.yml/badge.svg)](https://github.com/no1sebomb/sqlalchemy-ergo/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sqlalchemy-ergo.svg)](https://pypi.org/project/sqlalchemy-ergo/)
[![Python](https://img.shields.io/pypi/pyversions/sqlalchemy-ergo.svg)](https://pypi.org/project/sqlalchemy-ergo/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-d71f00.svg)](https://www.sqlalchemy.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE.md)

Ergonomic helpers for SQLAlchemy 2.0 ORM models — small, focused tools that remove
the boilerplate you end up rewriting in every project.

> **Status: pre-release.** The API is not stable yet.

## Installation

```bash
pip install sqlalchemy-ergo
```

## Helpers

### `counter_property`

A descriptor that counts the rows on the other side of a relationship, picking the
cheapest source available at access time.

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy_ergo import counter_property


class Base(DeclarativeBase): ...


class Human(Base):
    __tablename__ = "human"

    id: Mapped[int] = mapped_column(primary_key=True)
    books: Mapped[list["Book"]] = relationship()

    total_books = counter_property(books)
```

| access                                      | behaviour                                |
|---------------------------------------------|------------------------------------------|
| `human.total_books`, `books` already loaded | `len(human.books)` — no SQL              |
| `human.total_books`, `books` not loaded     | correlated `SELECT count(*)`, one query  |
| `human.total_books`, unsaved object         | counts the in-memory collection — no SQL |
| `where` that cannot be evaluated in Python  | always the SQL count, warned about once  |
| `Human.total_books`                         | the mapped `column_property` attribute   |

Because class access gives back a real mapped attribute, it composes with the rest
of the ORM:

```python
select(Human).order_by(Human.total_books.desc()).limit(10)
select(Human).where(Human.total_books > 0)
select(Human).options(undefer(Human.total_books))  # count comes with the row
```

Leave the attribute unannotated — instance access is already typed as `int` and
class access as `InstrumentedAttribute[int]`. An explicit `: int` annotation would
hide the latter from type checkers.

#### Options

```python
class Human(Base):
    books: Mapped[list["Book"]] = relationship()
    tags: Mapped[list["Tag"]] = relationship(secondary=human_tag)

    # Narrow the count down. The Python-side predicate is derived from the SQL
    # expression, so both access paths filter by one and the same rule.
    total_active_books = counter_property(
        books,
        where="Book.active.is_(True)",  # or a SQL expression / callable
    )

    # Many-to-many works the same way
    total_tags = counter_property(tags)

    # Loaded eagerly with every SELECT instead of on first access
    total_books_eager = counter_property(books, deferred=False)
```

| argument     | meaning                                                                                |
|--------------|----------------------------------------------------------------------------------------|
| `relation`   | relationship to count; one-to-many or many-to-many                                     |
| `secondary`  | extra relationship over the same rows — if it happens to be loaded, its length is used |
| `where`      | extra SQL condition: expression, callable, or string resolved against the registry     |
| `where_func` | optional Python predicate overriding the one derived from `where`                      |
| `deferred`   | whether the underlying `column_property` is deferred (default `True`)                  |
| `strict`     | cross-check every Python count against the database (default `False`)                  |

Self-referential and joined-table-inheritance relationships are supported; the
counted entity is aliased, so a self-join counts the right rows.

#### How `where` stays consistent

`where` is written once, as SQL. The Python predicate used for a loaded collection
is derived from it with the same evaluator SQLAlchemy uses for
`synchronize_session="evaluate"`, so the two paths cannot drift apart.

Column comparisons, `and_`/`or_`/`not_`, `is_`, and `in_` all translate. `LIKE`,
SQL functions, and subqueries do not — those warn once at mapper configuration
and fall back to reading the count from the database every time, which is correct
but costs a query. Pass `where_func` to take that path back:

```python
total_epics = counter_property(
    books,
    where=lambda: Book.title.like("epic%"),
    where_func=lambda book: book.title.startswith("epic"),
)
```

#### `strict`

A loaded collection is not always the whole collection. A filtered eager load
returns fewer rows than the count would:

```python
select(Human).options(selectinload(Human.books.and_(Book.active.is_(True))))
```

`total_books` would then report the filtered number without complaint.
`strict=True` recomputes the count in SQL on every read and raises
`CounterMismatchError` when the two disagree. It costs a query per access — turn
it on in tests, not in production.

#### asyncio

Under `AsyncSession` the no-SQL paths work unchanged, and `undefer()` loads the
count with the row. The lazy fallback does not work: emitting the count on
attribute access is implicit IO, which asyncio forbids — the same rule that makes
lazy relationship loads raise `MissingGreenlet`. Eager-load the relationship or
undefer the counter:

```python
await session.scalars(select(Human).options(undefer(Human.total_books)))
await session.scalars(select(Human).options(selectinload(Human.books)))
```

#### Known limitations

* Mixins are not supported — declare counters on the mapped class itself.
  `__set_name__` fires on the mixin, which has no mapper to attach to.
* Without `strict=True`, a filtered eager load is counted as-is and reports the
  filtered number.
* `where` resolution and predicate derivation both use private SQLAlchemy APIs
  (`orm.clsregistry._resolver`, `orm.evaluator`). A test asserts they are still
  importable, so a breaking upgrade fails CI rather than degrading silently.

## Requirements

* Python 3.10+
* SQLAlchemy 2.0+

## Development

```bash
pip install -e ".[asyncio]" --group dev
pre-commit install

pytest
ruff check .
mypy
```

## License

GPL-3.0-or-later. See [LICENSE.md](LICENSE.md).
