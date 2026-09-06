"""``CounterProperty`` / ``counter_property`` — count rows on the other side of a relationship.

The descriptor exposes an attribute that answers "how many related rows are
there?" using the cheapest source available at access time:

* the relationship is already loaded -> ``len(...)``, no SQL
* the relationship is not loaded -> a deferred ``column_property`` wrapping a
  correlated ``SELECT count(*)``
* class access (``Human.total_books``) -> the mapped ``column_property``
  attribute itself, so it works with ``undefer()``, ``order_by()``,
  ``where()`` and friends

The public attribute is a plain (non-mapped) descriptor; the ``column_property``
it delegates to is registered under a private name (``_<name>_counter``) on the
same class. Registration happens on the ``Mapper.after_configured`` event, i.e.
as soon as every mapper is resolved and before the first query, which is what
makes ``deferred`` behave correctly.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, overload

from sqlalchemy import and_, event, func, inspect, select
from sqlalchemy.orm import (
    Mapper,
    RelationshipProperty,
    aliased,
    column_property,
    configure_mappers,
    object_session,
)
from sqlalchemy.sql.elements import BooleanClauseList
from sqlalchemy.sql.util import ClauseAdapter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import InstrumentedAttribute
    from sqlalchemy.sql import ColumnElement, FromClause

try:  # pragma: no cover - depends on the installed SQLAlchemy build
    from sqlalchemy.orm.clsregistry import _resolver
except ImportError:  # pragma: no cover
    _resolver = None  # type: ignore[assignment]

try:  # pragma: no cover - depends on the installed SQLAlchemy build
    from sqlalchemy.orm import evaluator as _evaluator_module
except ImportError:  # pragma: no cover
    _evaluator_module = None  # type: ignore[assignment]

# Deliberately untyped: it is a private module that may disappear on upgrade,
# so every use of it goes through the None guard below
_evaluator: Any = _evaluator_module

__all__ = ["CounterMismatchError", "CounterProperty", "counter_property"]


class CounterMismatchError(RuntimeError):
    """Raised in ``strict`` mode when the Python and SQL counts disagree."""


class _UnevaluatableError(Exception):
    """Internal: this object cannot be judged in Python, fall back to SQL."""


class CounterProperty:
    """Count related objects, defined from a relationship instead of a subquery.

    Works with 'one-to-many' and 'many-to-many' relationships.

    Examples:
        >>> class Model(Base):
        ...     __tablename__ = "my_model"
        ...
        ...     objects: Mapped[list[RelatedModel]] = relationship("RelatedModel")
        ...     total_objects = counter_property(objects)

        Load it as a column property -- one query, no lazy loads afterward::

        >>> m = session.scalars(
        ...     select(Model).options(undefer(Model.total_objects))
        ... ).first()
        >>> m.total_objects

        Or let it read an already loaded relationship, again without extra SQL::

        >>> m = session.scalars(
        ...     select(Model).options(selectinload(Model.objects))
        ... ).first()
        >>> m.total_objects

        Counting can be narrowed down, and many-to-many works the same way::

        >>> class Model(Base):
        ...     __tablename__ = "my_model"
        ...
        ...     objects: Mapped[list[RelatedModel]] = relationship("RelatedModel")
        ...     total_objects = counter_property(objects)
        ...     total_active_objects = counter_property(
        ...         objects,
        ...         where="RelatedModel.is_active.is_(True)",
        ...     )
        ...
        ...     many_objects: Mapped[list[M2MModel]] = relationship(
        ...         "M2MModel", secondary="related_model"
        ...     )
        ...     total_many_objects = counter_property(many_objects)
    """

    def __init__(
        self,
        relation: RelationshipProperty[Any] | Any,
        secondary: RelationshipProperty[Any] | Any | None = None,
        where: str | ColumnElement[bool] | Callable[[], ColumnElement[bool]] | None = None,
        where_func: Callable[[Any], bool] | None = None,
        deferred: bool = True,
        strict: bool = False,
    ) -> None:
        """Initialize counter property from a relationship.

        Args:
            relation:
                Relationship to count objects from.
            secondary:
                Extra relationship pointing at the same objects. If it happens to
                be loaded while `relation` is not, it is counted instead of
                emitting a query.
            where:
                Additional WHERE clause for counting objects, as a SQL expression,
                a callable returning one, or a string evaluated against the
                declarative registry (`"RelatedModel.is_active.is_(True)"`).
            where_func:
                `where` as a plain Python predicate over a related object, used
                when the relationship is loaded. Optional: it is derived from
                `where` automatically whenever SQLAlchemy can evaluate that
                expression in Python. Pass it to override the derived one, or to
                keep the no-SQL path for a `where` that cannot be evaluated
                (SQL functions, LIKE, subqueries).
            deferred:
                Whether the underlying column property is deferred by default.
            strict:
                Cross-check every Python-side count against the SQL one and raise
                `CounterMismatchError` if they disagree. Costs a query per access,
                meant for tests and development.
        """

        if not isinstance(relation, RelationshipProperty):
            # Invalid type
            raise TypeError("CounterProperty must be initialized from a relationship")

        if secondary is not None and not isinstance(secondary, RelationshipProperty):
            # Invalid type
            raise TypeError("CounterProperty 'secondary' parameter must be a relationship")

        self.relationship: RelationshipProperty[Any] = relation
        self.secondary: RelationshipProperty[Any] | None = secondary
        self.deferred = deferred
        self.where = where
        self.where_func = where_func
        self.strict = strict

        self.name: str = ""
        self.owner: type[Any] | None = None
        self.column_property_name: str = ""
        self._initialized = False
        # Python-side predicate, resolved once the mappers are configured.
        # None means "counting in Python is not possible, always use SQL".
        self._predicate: Callable[[Any], bool] | None = None

    def __set_name__(self, owner: type[Any], name: str) -> None:
        """Initialize descriptor parameters and queue the column property."""

        self.name = name
        self.owner = owner
        self.column_property_name = f"_{name}_counter"

        # The relationship is not resolved yet at class definition time, so the
        # column property is built once every mapper is configured.
        _schedule(self)

    # -- column property construction -------------------------------------------------

    def _build_source(self) -> tuple[FromClause, ColumnElement[bool], ClauseAdapter]:
        """Build what to count from, and the condition tying it to the parent row.

        The target entity is always aliased. Without an alias a self-referential
        relationship would correlate both sides of its primaryjoin to the outer
        row (``human.id = human.parent_id`` on one and the same row), so the count
        would come back as 0.

        Returns the FROM clause, the WHERE clause, and the adapter that rewrites
        target columns onto the alias -- the extra ``where`` needs it too.
        """

        relation = self.relationship
        target = aliased(relation.entity)
        target_selectable: FromClause = inspect(target).selectable
        adapter = ClauseAdapter(target_selectable)

        if relation.secondary is None:
            # One-to-many: the target side of the primaryjoin is the annotated
            # 'remote' one; the local side stays put and correlates to the parent
            remote_adapter = ClauseAdapter(
                target_selectable,
                include_fn=lambda element: "remote" in element._annotations,
            )
            return target_selectable, remote_adapter.traverse(relation.primaryjoin), adapter

        # Many-to-many: join the association table in, so that the primaryjoin
        # (parent -> association) can be used as the WHERE clause as-is
        from_clause = target_selectable.join(
            relation.secondary,
            adapter.traverse(relation.secondaryjoin),
        )
        return from_clause, relation.primaryjoin, adapter

    def _resolve_where(self, owner: type[Any]) -> ColumnElement[bool] | None:
        """Resolve the additional WHERE clause into a SQL expression."""

        where = self.where

        if where is None:
            return None

        if isinstance(where, str):
            if _resolver is None:  # pragma: no cover - very old/new SQLAlchemy
                raise TypeError(
                    "String 'where' clauses are not supported with this SQLAlchemy version, "
                    "pass a SQL expression or a callable returning one"
                )

            # Evaluate against the declarative registry of the owning class
            _, resolve_arg = _resolver(owner, self.relationship)
            return resolve_arg(where, False)()  # type: ignore[no-any-return]

        if callable(where):
            return where()

        return where

    def _compile_predicate(
        self, expression: ColumnElement[bool] | None
    ) -> Callable[[Any], bool] | None:
        """Derive the Python-side predicate that mirrors `where`.

        Returns ``None`` when counting in Python is not possible, which makes the
        descriptor fall back to the SQL count -- correct, just not free.
        """

        if self.where_func is not None:
            # Explicitly written by hand, takes precedence over the derived one
            return self.where_func

        if expression is None:
            # Nothing to filter by, the collection is counted as a whole
            return None

        if _evaluator is None:  # pragma: no cover - very old/new SQLAlchemy
            self._warn_unevaluatable("this SQLAlchemy version exposes no expression evaluator")
            return None

        try:
            # The same machinery as synchronize_session="evaluate"
            evaluate = _evaluator._EvaluatorCompiler(self.relationship.mapper.class_).process(
                expression
            )
        except _evaluator.UnevaluatableError as error:
            self._warn_unevaluatable(str(error))
            return None

        def predicate(obj: Any) -> bool:
            result = evaluate(obj)

            if not isinstance(result, bool):
                # Expired or missing attribute: the evaluator hands back a
                # sentinel rather than a value, so this object cannot be judged
                raise _UnevaluatableError

            return result

        return predicate

    def _warn_unevaluatable(self, reason: str) -> None:
        """Tell the user the no-SQL path is off, and how to get it back."""

        warnings.warn(
            f"CounterProperty '{self.name}': the 'where' clause cannot be evaluated in Python "
            f"({reason}), so the count is always read from the database, even when the "
            f"relationship is loaded. Pass 'where_func' to restore the no-query path.",
            stacklevel=2,
        )

    def _warn_on_duplicated_conditions(
        self,
        join_clause: ColumnElement[bool],
        where_clause: ColumnElement[bool],
    ) -> None:
        """Warn when the extra WHERE clause repeats a condition from the primaryjoin."""

        for condition in _iter_conditions(join_clause):
            for additional_condition in _iter_conditions(where_clause):
                if condition.compare(additional_condition):
                    # Duplicated condition
                    warnings.warn(
                        f"Duplicated condition in CounterProperty 'where' and relationship "
                        f"'primaryjoin' parameters ('{condition}')",
                        stacklevel=4,
                    )

    def _initialize_column_property(self) -> None:
        """Create the underlying column property on the owning class."""

        if self._initialized:
            # Already initialized
            return

        owner = self.owner

        if owner is None:  # pragma: no cover - only if used outside a class body
            raise RuntimeError("CounterProperty must be assigned to a class attribute")

        if inspect(owner, raiseerr=False) is None:
            raise TypeError(
                f"CounterProperty {owner.__name__}.{self.name} is defined on a class that is "
                f"not mapped; declare it directly on the mapped class"
            )

        self._validate_relationships()

        from_clause, where_clause, adapter = self._build_source()
        additional_where_clause = self._resolve_where(owner)

        # Derived from the unadapted expression: the evaluator matches columns
        # against the real class, not against the alias the subquery counts from
        self._predicate = self._compile_predicate(additional_where_clause)

        if additional_where_clause is not None:
            # Written against the target class, so it needs the same aliasing
            additional_where_clause = adapter.traverse(additional_where_clause)
            self._warn_on_duplicated_conditions(where_clause, additional_where_clause)
            where_clause = and_(where_clause, additional_where_clause)

        expression = (
            select(func.count()).select_from(from_clause).where(where_clause).label(self.name)
        )

        # Register as a real mapped attribute, so it can be undeferred, ordered
        # and filtered on. Declarative forwards this to Mapper.add_property.
        mapped_property = column_property(expression, deferred=self.deferred)
        # InstrumentedAttribute has __slots__, so the descriptor is published
        # through the property's info dict instead of an attribute on it
        mapped_property.info["counter_property"] = self
        setattr(owner, self.column_property_name, mapped_property)
        self._initialized = True

    def _validate_relationships(self) -> None:
        """Check relationship kinds. Only possible once mappers are configured."""

        if not self.relationship.uselist:
            # Not a list
            raise TypeError(
                "CounterProperty must be initialized from a list relationship (one-to-many)"
            )

        if self.secondary is not None and not self.secondary.uselist:
            # Not a list
            raise TypeError("CounterProperty 'secondary' must be a list relationship (one-to-many)")

    def _is_ready(self) -> bool:
        """Whether the owning mapper is configured enough to build the property."""

        if self.owner is None:  # pragma: no cover - only outside a class body
            return False

        mapper = inspect(self.owner, raiseerr=False)

        return mapper is not None and bool(mapper.configured)

    def _ensure_initialized(self) -> None:
        """Make sure the column property exists before it is read or written."""

        if self._initialized:
            return

        # Configuring the mappers fires the event that drains the pending queue
        configure_mappers()

        if not self._initialized:  # pragma: no cover - defensive
            self._initialize_column_property()

    # -- descriptor protocol ----------------------------------------------------------

    @overload
    def __get__(self, instance: None, owner: type[Any]) -> InstrumentedAttribute[int]: ...

    @overload
    def __get__(self, instance: Any, owner: type[Any]) -> int: ...

    def __get__(self, instance: Any | None, owner: type[Any]) -> int | InstrumentedAttribute[int]:
        """Get value of this counter property (total count of related objects)."""

        self._ensure_initialized()

        if instance is None:
            # No instance (calling from a class) => return the mapped attribute,
            # so it can be undeferred, ordered and filtered on. The descriptor is
            # reachable from it as `attribute.info["counter_property"]`
            instrumented_attribute: InstrumentedAttribute[int] = getattr(
                owner, self.column_property_name
            )

            return instrumented_attribute

        state = inspect(instance)
        loaded_key = self._loaded_collection_key(state)

        if loaded_key is not None:
            counted = self._count_collection(getattr(instance, loaded_key))

            if counted is not None:
                if self.strict and state.has_identity:
                    self._verify_against_sql(instance, counted)

                return counted

            if not state.has_identity:
                # Nothing to fall back to: no row to count, no usable predicate
                raise RuntimeError(
                    f"CounterProperty '{self.name}' cannot be read from an unsaved object: "
                    f"its 'where' clause cannot be evaluated in Python. Pass 'where_func', "
                    f"or read the value once the object has been flushed"
                )

        # Use column property
        return int(getattr(instance, self.column_property_name))

    def _loaded_collection_key(self, state: Any) -> str | None:
        """Name of a collection that can be counted without touching the database."""

        if self.relationship.key not in state.unloaded:
            # Relationship is loaded => use it
            return str(self.relationship.key)

        if self.secondary is not None and self.secondary.key not in state.unloaded:
            # Secondary relationship is loaded => use it instead
            return str(self.secondary.key)

        if not state.has_identity:
            # Transient or pending object: there is no row to count against yet,
            # so read the (possibly empty) collection instead of emitting SQL
            return str(self.relationship.key)

        return None

    def _count_collection(self, related_objects: Any) -> int | None:
        """Count a loaded collection, or ``None`` if that cannot be done here."""

        if self.where is None and self.where_func is None:
            # No additional conditions => just use total length
            return len(related_objects)

        if self._predicate is None:
            # Filtering is required but not possible in Python
            return None

        try:
            # Calculate with condition
            return sum(1 for obj in related_objects if self._predicate(obj))
        except _UnevaluatableError:
            return None

    def _verify_against_sql(self, instance: Any, counted: int) -> None:
        """Cross-check the Python count against the database one (`strict` mode)."""

        session = object_session(instance)

        if session is not None:
            # Drop any cached value, so the check runs against a fresh count
            session.expire(instance, [self.column_property_name])

        from_sql = int(getattr(instance, self.column_property_name))

        if from_sql != counted:
            raise CounterMismatchError(
                f"CounterProperty '{self.name}' disagrees with the database: counted "
                f"{counted} from the loaded relationship, but the query returns {from_sql}. "
                f"The loaded collection is most likely filtered or out of date"
            )

    def __set__(self, instance: Any, value: int) -> None:
        """Set value of this counter property.

        Used to set the value automatically from a query result by SQLAlchemy.
        """

        self._ensure_initialized()

        # Set new value for property
        setattr(instance, self.column_property_name, value)


def counter_property(
    relation: RelationshipProperty[Any] | Any,
    secondary: RelationshipProperty[Any] | Any | None = None,
    where: str | ColumnElement[bool] | Callable[[], ColumnElement[bool]] | None = None,
    where_func: Callable[[Any], bool] | None = None,
    deferred: bool = True,
    strict: bool = False,
) -> CounterProperty:
    """Build a :class:`CounterProperty`. See its docstring for the full description.

    Leave the attribute unannotated -- the descriptor overloads already type
    instance access as ``int`` and class access as ``InstrumentedAttribute[int]``.
    An explicit ``total_objects: int = ...`` annotation would hide the latter.
    """

    return CounterProperty(
        relation,
        secondary=secondary,
        where=where,
        where_func=where_func,
        deferred=deferred,
        strict=strict,
    )


# -- deferred configuration -------------------------------------------------------------

_PENDING: list[CounterProperty] = []
_CONFIGURING = False


def _schedule(prop: CounterProperty) -> None:
    """Queue a counter property to be built once all mappers are configured."""

    _PENDING.append(prop)


def _configure_pending(*_: Any) -> None:
    """Build every queued counter property that is ready.

    Runs on ``Mapper.after_configured``. Configuration happens per registry, not
    globally: a project with more than one declarative base gets this event while
    the other base's mappers are still unconfigured, and their relationships have
    no ``primaryjoin`` or ``uselist`` yet. Those stay queued for a later pass.
    """

    global _CONFIGURING

    if _CONFIGURING:
        # Adding properties can re-enter mapper configuration
        return

    _CONFIGURING = True
    try:
        pending, _PENDING[:] = list(_PENDING), []

        for prop in pending:
            if prop._is_ready():
                prop._initialize_column_property()
            else:
                # Belongs to a registry that has not been configured yet
                _PENDING.append(prop)
    finally:
        _CONFIGURING = False


event.listen(Mapper, "after_configured", _configure_pending)


def _iter_conditions(clause: ColumnElement[bool]) -> Iterator[ColumnElement[bool]]:
    """Flatten an AND/OR clause list into its individual conditions."""

    if isinstance(clause, BooleanClauseList):
        yield from clause.clauses
    else:
        yield clause
