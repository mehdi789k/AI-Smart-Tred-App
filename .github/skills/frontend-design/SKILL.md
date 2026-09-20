# Frontend Design

## Purpose

Design clean, accessible, responsive, and production-ready frontend experiences for web apps.
This skill is intended to work with GitHub Copilot in VS Code and GitHub.com when placed under `.github/skills/<skill-name>/SKILL.md`.

## Core goals

- Turn product requirements into clear UI structure and interaction patterns.
- Prefer simple, scalable, maintainable interfaces over flashy or overly complex layouts.
- Keep the experience accessible, mobile-friendly, and consistent with the existing design system.
- Suggest implementation details that fit the repo's stack and architecture.
- Balance visual quality with performance, readability, and maintainability.

## Required workflow

1. Understand the user goal and target audience.
2. Inspect the existing app structure, styling system, routes, and relevant components.
3. Identify the minimal UI needed for the task and define layout, hierarchy, states, and interactions.
4. Choose a design pattern that matches the app's architecture and constraints.
5. Produce or update the necessary frontend code with accessibility and responsiveness in mind.
6. Validate that the UI works logically, is consistent, and does not introduce unnecessary complexity.
7. For a visual change, verify both the desktop and narrow-screen layout when a browser
   preview or existing UI test is available.

## Decision framework

### Layout
- Prefer clear information hierarchy and strong spacing.
- Keep primary actions obvious and easy to scan.
- Match the app's domain: dashboards, forms, tables, alerts, and workflows should feel purpose-built.

### UX
- Give users feedback for loading, empty, error, and success states.
- Make forms explicit, forgiving, and easy to validate.
- Avoid unnecessary modals, hidden actions, or vague labels.
- Keep destructive or financially consequential actions behind an explicit review step.
- Show the freshness and source of live data when that context affects a decision.

### Accessibility
- Use semantic HTML, focus states, and readable contrast.
- Ensure controls are keyboard accessible and have clear labels.
- Support reduced-motion and responsive layouts where relevant.
- Do not communicate financial state by color alone; pair color with text, icons, or values.
- Preserve readable tabular content on small screens with an intentional responsive pattern.

### Performance
- Prefer efficient rendering and modest component complexity.
- Avoid large unnecessary dependencies for simple interactions.
- Keep data-heavy views readable and structured.

### Maintainability
- Reuse components and patterns already present in the codebase.
- Keep styling predictable and aligned with established conventions.
- Write components that are easy to reason about and extend.

## Trading dashboard patterns

When designing a trading dashboard, organize the page around decisions and risk rather
than decoration:

- Put account health, circuit-breaker state, open exposure, and daily P&L near the top.
- Separate market context, signal information, and order controls into distinct regions.
- Give each metric a label, unit, timestamp or period, and a clear unavailable state.
- Use tables for exact values and charts for trends; do not make a chart the only place
  where a critical value can be read.
- Make symbol, timeframe, position size, stop-loss, take-profit, and estimated risk
  visible before an order can be reviewed.
- Show stale, disconnected, or delayed market data as an explicit operational state.

## State model

For every data-driven component, account for these states where applicable:

- loading: reserve space and identify what is being loaded
- ready: show the value, unit, time context, and relevant action
- empty: explain why there is no data and what the user can do next
- stale: identify the last update and avoid implying that the value is current
- error: explain the failed operation without masking the failure
- blocked: explain which safety rule or prerequisite prevents an action

## Order-flow safety

For order-entry or position-management UI:

1. Validate required inputs and ranges before submission.
2. Display the resolved symbol, direction, size, price context, and protective levels.
3. Present estimated exposure and relevant risk limits before confirmation.
4. Require an explicit confirmation for submission, cancellation, or emergency actions.
5. Disable duplicate submission while a request is in progress.
6. Report the server result and correlation/reference information after completion.

The UI must not bypass backend authorization, symbol whitelists, magic-number checks,
position limits, or daily-loss circuit breakers. A disabled control must explain why it
is disabled when the reason is actionable.

## Visual language

- Prefer a restrained palette with a neutral base and semantic status colors.
- Use positive/negative colors consistently, but include text labels or symbols.
- Reserve strong warning and danger treatments for actionable risk.
- Use consistent spacing, border, typography, and focus conventions from the existing UI.
- Do not introduce a new component library or design system when an existing one fits.

## Output expectations

When asked to design or implement UI work, provide:

- a brief understanding of the product goal and user need
- the recommended page/component structure
- the key interactions and state transitions
- a concrete implementation approach
- any trade-offs or assumptions
- code when relevant, with small, focused changes

## Safety and quality rules

- Do not invent backend APIs or data contracts unless the user approves them.
- Do not hide critical actions or business logic behind unclear UI patterns.
- Do not over-engineer simple interfaces.
- Prefer using existing components and design patterns before creating new abstractions.
- For financial or high-risk flows, include clear validation, warnings, and fail-safe states.

## Example prompts

- "Design a responsive trading dashboard for watchlists, orders, and P&L."
- "Create a clean login page with validation and accessible error states."
- "Build a settings screen with dark mode, form validation, and saved state."
- "Improve this table UI for readability, filtering, and mobile responsiveness."

## Implementation notes for this repo

This project is a trading system and should favor:

- clear dashboards and data density controls
- visible risk and status states
- strong contrast and legibility for live metrics
- simple but trustworthy order entry flows
- caution messaging for high-risk actions
- components that support monitoring and operational awareness

When working in this repo, keep the frontend aligned with the project's architecture and safety requirements, especially around financial actions and operational data.
