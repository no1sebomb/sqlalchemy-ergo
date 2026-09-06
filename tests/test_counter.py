"""Tests for ``counter_property``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Column, Engine, ForeignKey, Table, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    foreign,
    mapped_column,
    relationship,
    remote,
    selectinload,
    undefer,
)

from sqlalchemy_ergo import CounterMismatchError, CounterProperty, counter_property
from tests.conftest import QueryCounter


class Base(DeclarativeBase):
    pass


human_tag = Table(
    "human_tag",
    Base.metadata,
    Column("human_id", ForeignKey("human.id"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id"), primary_key=True),
)

friendship = Table(
    "friendship",
    Base.metadata,
    Column("left_id", ForeignKey("human.id"), primary_key=True),
    Column("right_id", ForeignKey("human.id"), primary_key=True),
)


class Human(Base):
    __tablename__ = "human"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("human.id"))
    kind: Mapped[str] = mapped_column(default="human")

    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "human"}

    books: Mapped[list[Book]] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=human_tag)
    children: Mapped[list[Human]] = relationship()
    ebooks: Mapped[list[Ebook]] = relationship(viewonly=True)
    # Self-referential many-to-many: both join sides point at `human`
    friends: Mapped[list[Human]] = relationship(
        secondary=friendship,
        primaryjoin=lambda: Human.id == friendship.c.left_id,
        secondaryjoin=lambda: Human.id == friendship.c.right_id,
    )
    # Same rows as `books`, used to exercise the `secondary` fallback
    books_view: Mapped[list[Book]] = relationship(viewonly=True)

    total_books = counter_property(books)
    total_books_eager = counter_property(books, deferred=False)
    # No where_func: the Python predicate is derived from the SQL one
    total_active_books = counter_property(books, where="Book.active.is_(True)")
    total_active_books_manual = counter_property(
        books,
        where="Book.active.is_(True)",
        where_func=lambda book: book.active,
    )
    total_books_strict = counter_property(books, strict=True)
    total_tags = counter_property(tags)
    total_children = counter_property(children)
    total_friends = counter_property(friends)
    total_ebooks = counter_property(ebooks)
    total_books_or_view = counter_property(books, books_view)


class Reader(Human):
    """Joined-table subclass, inherits the counters from `Human`."""

    __tablename__ = "reader"

    id: Mapped[int] = mapped_column(ForeignKey("human.id"), primary_key=True)
    card: Mapped[str] = mapped_column(default="")

    __mapper_args__ = {"polymorphic_identity": "reader"}


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    human_id: Mapped[int] = mapped_column(ForeignKey("human.id"))
    active: Mapped[bool] = mapped_column(default=True)
    kind: Mapped[str] = mapped_column(default="book")

    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "book"}


class Ebook(Book):
    """Joined-table subclass, used as a counted target."""

    __tablename__ = "ebook"

    id: Mapped[int] = mapped_column(ForeignKey("book.id"), primary_key=True)
    format: Mapped[str] = mapped_column(default="epub")

    __mapper_args__ = {"polymorphic_identity": "ebook"}


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(primary_key=True)


@pytest.fixture(autouse=True)
def schema(engine: Engine) -> Iterator[None]:
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def data(session: Session) -> Human:
    """One human with 3 books (2 active), 2 tags and 1 child."""

    human = Human(
        name="reader",
        books=[Book(active=True), Book(active=True), Book(active=False)],
        tags=[Tag(), Tag()],
        children=[Human(name="child")],
    )
    session.add(human)
    session.add(Human(name="loner"))
    session.commit()
    session.expunge_all()

    return human


def _fresh(session: Session, name: str = "reader") -> Human:
    return session.scalars(select(Human).where(Human.name == name)).one()


# -- reading the value ------------------------------------------------------------------


def test_unloaded_relationship_emits_single_count_query(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    human = _fresh(session)

    with counter:
        assert human.total_books == 3

    assert counter.count == 1
    assert "count" in counter.statements[0].lower()


def test_loaded_relationship_emits_no_query(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books))
    ).one()

    with counter:
        assert human.total_books == 3

    assert counter.count == 0


def test_undefer_loads_counter_with_the_row(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    with counter:
        human = session.scalars(
            select(Human).where(Human.name == "reader").options(undefer(Human.total_books))
        ).one()
        assert human.total_books == 3

    assert counter.count == 1


def test_deferred_false_is_loaded_eagerly(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    with counter:
        human = _fresh(session)
        assert human.total_books_eager == 3

    assert counter.count == 1


def test_empty_collection_counts_zero(session: Session, data: Human) -> None:
    loner = _fresh(session, "loner")

    assert loner.total_books == 0
    assert loner.total_tags == 0


def test_many_to_many(session: Session, counter: QueryCounter, data: Human) -> None:
    human = _fresh(session)

    with counter:
        assert human.total_tags == 2
    assert counter.count == 1

    session.expire(human)
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.tags))
    ).one()
    with counter:
        assert human.total_tags == 2
    assert counter.count == 0


def test_self_referential(session: Session, data: Human) -> None:
    assert _fresh(session).total_children == 1
    assert _fresh(session, "child").total_children == 0


def test_self_referential_many_to_many(session: Session, data: Human) -> None:
    reader = _fresh(session)
    child = _fresh(session, "child")
    reader.friends.append(child)
    session.commit()
    session.expunge_all()

    assert _fresh(session).total_friends == 1
    assert _fresh(session, "child").total_friends == 0


def test_joined_inheritance_target(session: Session, data: Human) -> None:
    human = _fresh(session)
    session.add(Ebook(human_id=human.id))
    session.commit()
    session.expunge_all()

    human = _fresh(session)
    assert human.total_ebooks == 1
    # The plain book counter sees the ebook row too
    assert human.total_books == 4


def test_subclass_inherits_the_counter(session: Session) -> None:
    reader = Reader(name="member", card="A1", books=[Book(), Book()])
    session.add(reader)
    session.commit()
    session.expunge_all()

    fetched = session.scalars(select(Reader)).one()
    assert fetched.total_books == 2
    assert session.scalars(select(Reader).where(Reader.total_books > 1)).one() is fetched


# -- additional conditions --------------------------------------------------------------


def test_where_clause_filters_the_count(session: Session, data: Human) -> None:
    assert _fresh(session).total_active_books == 2


def test_where_func_matches_the_query_when_loaded(session: Session, data: Human) -> None:
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books))
    ).one()

    assert human.total_active_books == 2


def test_secondary_relationship_used_when_loaded(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books_view))
    ).one()

    with counter:
        assert human.total_books_or_view == 3

    assert counter.count == 0


# -- class level expression -------------------------------------------------------------


def test_order_by(session: Session, data: Human) -> None:
    names = session.scalars(select(Human.name).order_by(Human.total_books.desc(), Human.name)).all()

    assert names[0] == "reader"


def test_where(session: Session, data: Human) -> None:
    names = set(session.scalars(select(Human.name).where(Human.total_books > 0)).all())

    assert names == {"reader"}


def test_class_access_returns_mapped_attribute() -> None:
    attribute = Human.total_books

    assert attribute.key == "_total_books_counter"
    assert isinstance(attribute.info["counter_property"], CounterProperty)


# -- assignment and unpersisted objects -------------------------------------------------


def test_set_value(session: Session, data: Human) -> None:
    human = _fresh(session)
    human.total_books = 99

    assert human.total_books == 99


def test_transient_instance_counts_in_python(counter: QueryCounter) -> None:
    human = Human(name="new", books=[Book()])

    with counter:
        assert human.total_books == 1
        assert Human(name="empty").total_books == 0

    assert counter.count == 0


# -- validation -------------------------------------------------------------------------


def test_rejects_non_relationship() -> None:
    with pytest.raises(TypeError, match="must be initialized from a relationship"):
        counter_property(Human.name)


def test_rejects_non_relationship_secondary() -> None:
    with pytest.raises(TypeError, match="'secondary' parameter must be a relationship"):
        counter_property(Human.books.property, Human.name)


def test_derived_predicate_matches_the_query(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    """`where` alone filters both the SQL count and the loaded collection."""

    assert _fresh(session).total_active_books == 2

    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books))
    ).one()

    with counter:
        assert human.total_active_books == 2

    assert counter.count == 0


def test_explicit_where_func_still_wins(
    session: Session, counter: QueryCounter, data: Human
) -> None:
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books))
    ).one()

    with counter:
        assert human.total_active_books_manual == 2

    assert counter.count == 0


def test_rejects_scalar_relationship() -> None:
    class ScalarBase(DeclarativeBase):
        pass

    class Parent(ScalarBase):
        __tablename__ = "parent"

        id: Mapped[int] = mapped_column(primary_key=True)
        child: Mapped[Child] = relationship(uselist=False)
        total = counter_property(child)

    class Child(ScalarBase):
        __tablename__ = "child"

        id: Mapped[int] = mapped_column(primary_key=True)
        parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id"))

    with pytest.raises(TypeError, match="list relationship"):
        _ = Parent.total


def test_warns_on_condition_duplicated_from_the_primaryjoin(engine: Engine) -> None:
    class DupBase(DeclarativeBase):
        pass

    class Owner(DupBase):
        __tablename__ = "owner"

        id: Mapped[int] = mapped_column(primary_key=True)
        items: Mapped[list[Item]] = relationship()
        total = counter_property(
            items,
            where=lambda: Item.owner_id == Owner.id,
            where_func=lambda item: True,
        )

    class Item(DupBase):
        __tablename__ = "item"

        id: Mapped[int] = mapped_column(primary_key=True)
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"))

    with pytest.warns(UserWarning, match="Duplicated condition"):
        _ = Owner.total


def test_unevaluatable_where_warns_and_falls_back_to_sql(engine: Engine) -> None:
    """A `where` SQLAlchemy cannot evaluate in Python stays correct, via a query."""

    class LikeBase(DeclarativeBase):
        pass

    class Shelf(LikeBase):
        __tablename__ = "shelf"

        id: Mapped[int] = mapped_column(primary_key=True)
        volumes: Mapped[list[Volume]] = relationship()
        # LIKE has no Python evaluator
        total_epics = counter_property(volumes, where=lambda: Volume.title.like("epic%"))

    class Volume(LikeBase):
        __tablename__ = "volume"

        id: Mapped[int] = mapped_column(primary_key=True)
        shelf_id: Mapped[int] = mapped_column(ForeignKey("shelf.id"))
        title: Mapped[str] = mapped_column(default="")

    with pytest.warns(UserWarning, match="cannot be evaluated in Python"):
        _ = Shelf.total_epics

    LikeBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Shelf(volumes=[Volume(title="epic one"), Volume(title="epic two"), Volume(title="x")])
        )
        session.commit()

    with Session(engine) as session:
        # Loaded relationship, but the predicate is unusable => still correct
        shelf = session.scalars(select(Shelf).options(selectinload(Shelf.volumes))).one()
        assert shelf.total_epics == 2


def test_strict_mode_accepts_agreeing_counts(session: Session, data: Human) -> None:
    human = session.scalars(
        select(Human).where(Human.name == "reader").options(selectinload(Human.books))
    ).one()

    assert human.total_books_strict == 3


def test_strict_mode_catches_a_filtered_eager_load(session: Session, data: Human) -> None:
    """The trap `strict` exists for: a filtered collection is not the whole count."""

    human = session.scalars(
        select(Human)
        .where(Human.name == "reader")
        .options(selectinload(Human.books.and_(Book.active.is_(True))))
    ).one()

    with pytest.raises(CounterMismatchError, match=r"counted 2 .* returns 3"):
        _ = human.total_books_strict


def test_private_sqlalchemy_api_is_available() -> None:
    """Fails loudly if a SQLAlchemy upgrade removes what the derivation relies on."""

    from sqlalchemy.orm.clsregistry import _resolver
    from sqlalchemy.orm.evaluator import UnevaluatableError, _EvaluatorCompiler

    assert callable(_resolver)
    assert callable(_EvaluatorCompiler)
    assert issubclass(UnevaluatableError, Exception)


def test_counters_from_another_registry_are_left_alone(engine: Engine) -> None:
    """Configuring one declarative base must not build another base's counters.

    Mapper configuration is per registry, so `after_configured` fires while a
    second base is still unconfigured -- its relationships have no `uselist` yet.
    """

    class FirstBase(DeclarativeBase):
        pass

    class SecondBase(DeclarativeBase):
        pass

    class Nest(FirstBase):
        __tablename__ = "nest"

        id: Mapped[int] = mapped_column(primary_key=True)
        eggs: Mapped[list[Egg]] = relationship()
        total_eggs = counter_property(eggs)

    class Egg(FirstBase):
        __tablename__ = "egg"

        id: Mapped[int] = mapped_column(primary_key=True)
        nest_id: Mapped[int] = mapped_column(ForeignKey("nest.id"))

    class Hive(SecondBase):
        __tablename__ = "hive"

        id: Mapped[int] = mapped_column(primary_key=True)
        bees: Mapped[list[Bee]] = relationship()
        total_bees = counter_property(bees)

    class Bee(SecondBase):
        __tablename__ = "bee"

        id: Mapped[int] = mapped_column(primary_key=True)
        hive_id: Mapped[int] = mapped_column(ForeignKey("hive.id"))

    # Configures FirstBase only, while Hive.total_bees is still queued
    nest = Nest(eggs=[Egg()])
    assert nest.total_eggs == 1

    FirstBase.metadata.create_all(engine)
    SecondBase.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Hive(bees=[Bee(), Bee()]))
        session.commit()

    with Session(engine) as session:
        assert session.scalars(select(Hive)).one().total_bees == 2


def test_extra_conditions_in_primaryjoin_are_counted(engine: Engine) -> None:
    """A relationship that filters in its own primaryjoin counts the filtered rows.

    The whole primaryjoin becomes the subquery's WHERE clause, and the loaded
    collection was already filtered by it, so both paths agree for free.
    """

    class SoftBase(DeclarativeBase):
        pass

    class Author(SoftBase):
        __tablename__ = "author"

        id: Mapped[int] = mapped_column(primary_key=True)
        is_deleted: Mapped[bool] = mapped_column(default=False)

        # Soft delete folded into the relationship itself
        papers: Mapped[list[Paper]] = relationship(
            primaryjoin=lambda: (Author.id == Paper.author_id) & Paper.is_deleted.is_(False),
            viewonly=True,
        )
        # Self-referential, with the sides marked explicitly
        students: Mapped[list[Author]] = relationship(
            primaryjoin=lambda: (
                (Author.id == remote(foreign(Author.mentor_id)))
                & remote(Author.is_deleted).is_(False)
            ),
            viewonly=True,
        )
        mentor_id: Mapped[int | None] = mapped_column(ForeignKey("author.id"))

        total_papers = counter_property(papers)
        total_students = counter_property(students)

    class Paper(SoftBase):
        __tablename__ = "paper"

        id: Mapped[int] = mapped_column(primary_key=True)
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        is_deleted: Mapped[bool] = mapped_column(default=False)

    SoftBase.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            Author(
                id=1,
                papers=[],
                # two live papers, one soft-deleted
            )
        )
        session.add_all(
            [
                Paper(author_id=1, is_deleted=False),
                Paper(author_id=1, is_deleted=False),
                Paper(author_id=1, is_deleted=True),
                Author(id=2, mentor_id=1, is_deleted=False),
                Author(id=3, mentor_id=1, is_deleted=True),
            ]
        )
        session.commit()

    # Counted in SQL: the alias carries the extra condition, not the parent row
    with Session(engine) as session:
        author = session.get(Author, 1)
        assert author is not None
        assert author.total_papers == 2
        assert author.total_students == 1

    # Counted in Python: the collection was already filtered on load
    with Session(engine) as session:
        author = session.scalars(
            select(Author)
            .where(Author.id == 1)
            .options(selectinload(Author.papers), selectinload(Author.students))
        ).one()

        assert author.total_papers == 2
        assert author.total_students == 1
