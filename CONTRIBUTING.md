# Contributing to funcnodes-worker

This repository contains the **worker runtime** used to execute FuncNodes graphs (WebSocket RPC server, loops, worker config).

## Development setup (Python)

Prereqs:

- Python **3.11+**
- `uv` (https://github.com/astral-sh/uv)

Recommended environment variables (keep caches/config local):

- `UV_CACHE_DIR=.cache/uv`
- `FUNCNODES_CONFIG_DIR=.funcnodes`

Install dev dependencies:

```bash
cd funcnodes_worker
UV_CACHE_DIR=.cache/uv uv sync --group dev
```

Run unit tests:

```bash
cd funcnodes_worker
FUNCNODES_CONFIG_DIR=.funcnodes UV_CACHE_DIR=.cache/uv uv run pytest
```

## Full test matrix (tox)

`funcnodes-worker` has optional extras (`venv`, `http`, `subprocess-monitor`) and a tox matrix to test combinations.

```bash
cd funcnodes_worker
FUNCNODES_CONFIG_DIR=.funcnodes UV_CACHE_DIR=.cache/uv uv run tox
```

## Code style & hooks

Run pre-commit:

```bash
cd funcnodes_worker
UV_CACHE_DIR=.cache/uv uv run pre-commit install
UV_CACHE_DIR=.cache/uv uv run pre-commit run -a
```

## TDD expectations

- Write tests first; cover edge cases.
- Avoid mocks unless simulating external resources.
