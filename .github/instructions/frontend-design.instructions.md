# Frontend Design Instructions

Use these instructions when designing or implementing frontend UI in this repository.

## Goals
- Produce interfaces that are clear, responsive, and professional.
- Favor user trust, accessibility, and operational clarity.
- Keep layouts simple, readable, and easy to maintain.

## Design priorities
- Make key actions visible and obvious.
- Structures should help users interpret metrics, risk, and status quickly.
- Prefer strong hierarchy and consistent spacing over decorative complexity.
- Support desktop and mobile layouts without breaking information flow.
- Treat live-data freshness, connection state, and risk limits as first-class UI state.

## Quality bar
- Use semantic, accessible markup.
- Ensure all interactive controls have labels and clear focus states.
- Show loading, empty, and error states explicitly.
- Keep data-dense panels readable and not visually overloaded.
- Avoid unsafe or confusing actions in financial workflows.
- Never rely on color alone to communicate profit, loss, warnings, or connectivity.
- Preserve explicit review and confirmation boundaries for financial actions.

## Repo-specific guidance
- Respect the project safety rules and financial-risk constraints.
- When designing trading or order flows, highlight risk and confirmation boundaries.
- Keep dashboard patterns consistent with operational monitoring workflows.
- Prefer incremental, maintainable UI improvements over large rewrites.
- For dashboards, surface account health, exposure, daily P&L, circuit-breaker state,
  and data timestamps before secondary charts or decorative elements.
- For order forms, show symbol, direction, size, protective levels, estimated risk,
  validation errors, and the final confirmation summary.
- Do not invent endpoints, fields, or permissions; inspect existing contracts first.
- Keep backend safety checks authoritative even when a frontend control is disabled.

## Response style
- Explain the UX decision in a short rationale.
- Describe the layout, interaction model, and state transitions.
- Suggest practical implementation steps and component boundaries.
- Keep the solution anchored to the existing project structure and codebase patterns.
- Mention assumptions and unresolved data-contract questions explicitly.
