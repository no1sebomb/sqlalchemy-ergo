# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-06

### Added

- Project scaffolding: packaging, linting, typing, CI, release workflow.
- `counter_property` / `CounterProperty` — counts the rows behind a relationship,
  reading a loaded collection when there is one and falling back to a correlated
  `SELECT count(*)` otherwise. Supports one-to-many, many-to-many,
  self-referential and joined-inheritance relationships, an extra `where`
  condition, a `secondary` relationship fallback, and `deferred`.
- `where_func` is derived from `where` automatically, using SQLAlchemy's
  expression evaluator, so the SQL and Python counts cannot drift apart. It is
  optional, and only needed to override the derived predicate or to keep the
  no-query path for an expression the evaluator cannot handle.
- `strict=True` cross-checks every Python-side count against the database and
  raises `CounterMismatchError` on disagreement, which catches filtered eager
  loads such as `selectinload(Human.books.and_(...))`.

### Known limitations

- Counters cannot be declared on mixins, only on mapped classes.
- Under `AsyncSession` the lazy fallback is unavailable; eager-load the
  relationship or `undefer()` the counter.
- `where` resolution and predicate derivation use private SQLAlchemy APIs
  (`orm.clsregistry._resolver`, `orm.evaluator`), covered by a test that fails if
  either disappears.

[Unreleased]: https://github.com/no1sebomb/sqlalchemy-ergo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/no1sebomb/sqlalchemy-ergo/releases/tag/v0.1.0
