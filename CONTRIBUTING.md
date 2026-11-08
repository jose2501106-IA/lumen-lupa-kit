# Contributing

> En español: abre un *issue* o un *pull request* pequeño con pruebas; antes, corre `uv sync --locked`,
> `uv run ruff check`, `uv run ruff format --check` y `uv run pytest -q`.

Contributions are welcome as issues or small pull requests with tests.

Before you open a pull request, run the same checks as CI:

```bash
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run pytest -q
```

- Tests use no network and no credentials: their data comes from the simulator or from fixtures. A test that
  queries BigQuery is marked `@pytest.mark.live` and does not run in CI.
- Never commit credentials, `.env` files or downloaded data (`data/`, `*.duckdb`, `*.jsonl` outside
  `tests/fixtures/`).
- Dependencies are pinned in `pyproject.toml` and `uv.lock`. Please discuss a new dependency in an issue first.
- Examples and fixtures use the control market (Circle USDC / native XLM) or synthetic data. Do not add
  judgments about real tokens or accounts, and do not generate on-chain activity to test anything.
- Security problems go through [SECURITY.md](SECURITY.md), not public issues.

Code, docs and messages are in Spanish; issues and pull requests in English or Spanish are both fine.
