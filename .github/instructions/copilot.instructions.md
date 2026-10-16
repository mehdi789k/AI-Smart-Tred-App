# Project: Smart MT5 Trading System (Local Development)

## Core Principles
- This is a **financial trading system**. Accuracy, reliability, and safety are critical.
- **NEVER** generate code that could cause real financial loss without explicit confirmation.
- All trading logic MUST include proper error handling, logging, and fail-safe mechanisms.
- Use **defensive programming**: validate every input, handle every exception.

## Tech Stack (Strict)
- **MT5 Side**: MQL5 (strict mode), build 4000+
- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0
- **Database**: PostgreSQL 15 + TimescaleDB extension
- **ML**: scikit-learn, XGBoost, PyTorch (for deep learning)
- **Communication**: ZeroMQ (pyzmq) between MQL5 and Python
- **Testing**: pytest (Python), MQL5 built-in Strategy Tester

## Code Style
- **Python**: Black formatter, isort, type hints mandatory, ruff linter
- **MQL5**: Follow MQL5 coding standards, use `#property strict`, proper indentation
- **Naming**: snake_case (Python), PascalCase (MQL5 classes/functions)
- **Comments**: Docstrings for every public function. Explain "WHY" not "WHAT"

## Safety Rules
- All order-sending functions MUST have:
  - Maximum position size limit
  - Maximum daily loss limit (circuit breaker)
  - Symbol whitelist
  - Magic number validation
- NO hardcoded credentials. Use `.env` files.
- All database operations must use transactions.

## File Ownership (CRITICAL)
- `/src/mql5/` → Only Order Executor agent should modify
- `/src/python/data/` → Only Data Collector agent
- `/src/python/indicators/` → Only Indicator Engine agent
- `/src/python/ml/` → Only ML Engine agent
- `/src/python/backtest/` → Only Backtest Engine agent
- `/src/python/trading_signal/` and `/src/python/risk/` → Only Signal & Risk agent
- **DO NOT** modify files outside your assigned directory without explicit permission.

## Project Organization and Root Hygiene (MANDATORY)
- Before creating or moving a file, inspect the existing project layout and reuse the
  appropriate directory instead of adding another top-level file or duplicate module.
- Keep the project root reserved for stable entry points, configuration, dependency
  manifests, repository metadata, and essential project documentation.
- Place implementation code under `src/` (or the established domain package), tests
  under `tests/`, operational scripts under `scripts/` or `ops/`, documentation under
  `docs/`, generated reports under `reports/`, model artifacts under `models/`, market
  data under `market_data/` or `data/`, and runtime logs under `logs/`.
- Never leave temporary files, caches, compiled bytecode, ad-hoc exports, debug output,
  generated reports, model artifacts, datasets, or runtime logs in the project root.
- Do not create new catch-all directories or duplicate existing packages. If a new
  category is genuinely required, document its purpose and place it under the closest
  existing category.
- When a task produces an artifact, save it in the appropriate existing directory and
  update the relevant documentation or ignore rules when necessary.
- At the end of every coding task, inspect the root and changed paths, remove only
  task-created temporary artifacts, and verify that the root remains clean and
  organized. Never delete user-owned files or unrelated existing artifacts.
- Treat this organization rule as part of every implementation, test, debugging, and
  refactoring task, including work performed by agents and hooks.

## Documentation
- Update `docs/DATA_CONTRACT.md` if you change data structures
- Update `docs/API_CONTRACT.md` if you add/modify endpoints
- Update `docs/ARCHITECTURE.md` if you change system design

## Systematic debugging
- Investigate before modifying code; do not guess or patch symptoms.
- Read the complete error output and reproduce the issue with an exact command.
- Inspect recent changes, configuration, environment propagation, and component boundaries.
- Trace failing data or control flow backward to the root cause.
- State one testable hypothesis and test it with the smallest possible change.
- Create or update a regression test before implementing the final fix when possible.
- Make one root-cause change at a time and avoid unrelated refactoring.
- Run the relevant existing validation commands and inspect their exit codes.
- Never claim a bug is fixed, or that tests/build/lint pass, without fresh evidence.
- After three failed fixes, stop and question the architecture rather than applying another
  symptom-level patch.