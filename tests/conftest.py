"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session


class QueryCounter:
    """Counts statements executed on an engine, so tests can assert on lazy loads."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    @property
    def count(self) -> int:
        return len(self.statements)

    def __enter__(self) -> QueryCounter:
        self.statements.clear()
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


@pytest.fixture
def counter(engine: Engine) -> Iterator[QueryCounter]:
    counter = QueryCounter()

    @event.listens_for(engine, "before_cursor_execute")
    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        counter.statements.append(statement)

    yield counter
    event.remove(engine, "before_cursor_execute", _record)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
