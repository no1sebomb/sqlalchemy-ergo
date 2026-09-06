# Contributing

## Setup

```bash
uv sync --group dev --extra asyncio
uv run pre-commit install
```

Without `uv`:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[asyncio]"
pip install pytest pytest-asyncio coverage mypy ruff pre-commit aiosqlite
```

## Checks

```bash
ruff check . && ruff format --check .
mypy
coverage run -m pytest && coverage report
```

All three run in CI on Python 3.10–3.13.

## Conventions

* `src/` layout — the installed package is what tests import.
* Public API lives in `sqlalchemy_ergo/__init__.__all__`. Modules prefixed with
  `_` are private and may change without notice.
* Every helper needs a test proving how many queries it emits — count them with
  the statement-capturing fixture, not by eyeballing echo output.
* Type annotations are mandatory in `src/`; `mypy --strict` must pass.

## Releasing

1. Bump `__version__` in `src/sqlalchemy_ergo/__init__.py`.
2. Move the `Unreleased` entries in `CHANGELOG.md` under the new version.
3. Tag `vX.Y.Z` and publish a GitHub Release — `publish.yml` uploads to PyPI
   via Trusted Publishing.
