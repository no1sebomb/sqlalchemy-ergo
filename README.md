# sqlalchemy-ergo

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
from sqlalchemy.orm import DeclarativeBase, Mapped, relationship
from sqlalchemy_ergo import counter_property


class Base(DeclarativeBase): ...


class Human(Base):
    __tablename__ = "human"

    id: Mapped[int] = mapped_column(primary_key=True)
    books: Mapped[list["Book"]] = relationship()

    total_books = counter_property(books)
```

| access | behaviour |
| --- | --- |
| `human.total_books`, `books` already loaded | `len(human.books)` — no SQL |
| `human.total_books`, `books` not loaded | correlated `SELECT count(*)` subquery |
| `Human.total_books` | the SQL expression, usable in `order_by` / `where` / `having` |

```python
session.scalars(
    select(Human).order_by(Human.total_books.desc()).limit(10)
)
```

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
