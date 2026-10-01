---
name: project-organization
description: Keep the Smart MT5 Trading System repository clean and well-structured by placing every new or generated file in the correct existing project directory. Use this skill when creating new files, generating reports, saving models, organizing code, or performing any file-system operation within the project. Keywords: project structure, directory map, file organization, repository layout, folder convention, clean root, file placement, Smart MT5, trading system.
---

<!-- Tip: Use /create-skill in chat to generate content with agent assistance -->

# Project Organization Skill

## Purpose
Keep the Smart MT5 Trading System easy to navigate by placing every new or generated file in the correct existing project category and keeping the repository root clean.

## Required Workflow
1. Inspect the relevant existing directories before creating a file.
2. Reuse the closest established directory and naming convention.
3. Keep implementation, tests, scripts, documentation, generated output, data, models, and logs separated.
4. Do not place temporary files, caches, compiled files, reports, datasets, model artifacts, or logs in the project root.
5. Before completing the task, review the root and changed paths and remove only temporary artifacts created during the task.

## Directory Map

| Content                                | Directory              |
|----------------------------------------|------------------------|
| Application and domain code            | `src/`                 |
| Tests                                  | `tests/`               |
| Documentation and contracts            | `docs/`                |
| Maintenance and training commands      | `scripts/`             |
| Operations and deployment helpers      | `ops/`                 |
| Generated reports and quality output   | `reports/`             |
| Trained model artifacts                | `models/`              |
| Historical/runtime market data         | `data/`, `market_data/`|
| Runtime logs                           | `logs/`                |
| Copilot instructions, agents, skills   | `.github/`             |

## Safety Rules
- Do not move or delete an existing file merely to make the layout look cleaner when it may be user-owned or referenced by runtime code.
- Ask for clarification before a broad reorganization.
- Prefer a small, explicit move with all imports, documentation, and deployment references updated together.

## Example Prompts
- "Where should I put a new backtesting script?"
- "Organize the generated CSV reports into the correct folder."
- "Clean up temporary files from the project root."
- "Add a new data pipeline module in the right directory."