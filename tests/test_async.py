"""Behavior under ``AsyncSession``.

The no-SQL paths work unchanged. The lazy fallback does not: emitting the count
on attribute access is implicit IO, which asyncio forbids, exactly like a lazy
relationship load. Eager-load the relationship or ``undefer()`` the counter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import ForeignKey, select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    selectinload,
    undefer,
)

from sqlalchemy_ergo import counter_property


class Base(DeclarativeBase):
    pass


class Human(Base):
    __tablename__ = "human"

    id: Mapped[int] = mapped_column(primary_key=True)
    books: Mapped[list[Book]] = relationship()

    total_books = counter_property(books)


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    human_id: Mapped[int] = mapped_column(ForeignKey("human.id"))


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite://")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine) as session:
        session.add(Human(books=[Book(), Book()]))
        await session.commit()

    yield engine
    await engine.dispose()


async def test_loaded_relationship_needs_no_io(async_engine: AsyncEngine) -> None:
    async with AsyncSession(async_engine) as session:
        human = (await session.scalars(select(Human).options(selectinload(Human.books)))).one()

        assert human.total_books == 2


async def test_undefer_loads_the_counter_with_the_row(async_engine: AsyncEngine) -> None:
    async with AsyncSession(async_engine) as session:
        human = (await session.scalars(select(Human).options(undefer(Human.total_books)))).one()

        assert human.total_books == 2


async def test_lazy_fallback_is_not_available(async_engine: AsyncEngine) -> None:
    """Documents the limitation: implicit IO on attribute access is not allowed."""

    async with AsyncSession(async_engine) as session:
        human = (await session.scalars(select(Human))).one()

        with pytest.raises(MissingGreenlet):
            _ = human.total_books
