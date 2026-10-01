---
name: frontend-design
description: Design clean, accessible, responsive, and production-ready frontend experiences for web apps, especially trading dashboards and financial systems. Use this skill when creating UI components, designing layouts, improving UX, implementing frontend code for dashboards, forms, tables, order-entry flows, and data-heavy views. Keywords: frontend, UI, UX, responsive, accessibility, trading dashboard, financial UI, component design, state management, order-flow safety, data visualization, dark mode, form validation.
---

<!-- Tip: Use /create-skill in chat to generate content with agent assistance -->

# Frontend Design Skill

## Purpose
Design clean, accessible, responsive, and production-ready frontend experiences for web apps.
This skill is intended to work with GitHub Copilot in VS Code and GitHub.com when placed under `.github/skills/<skill-name>/SKILL.md`.

## Core Goals
- Turn product requirements into clear UI structure and interaction patterns.
- Prefer simple, scalable, maintainable interfaces over flashy or overly complex layouts.
- Keep the experience accessible, mobile-friendly, and consistent with the existing design system.
- Suggest implementation details that fit the repo's stack and architecture.
- Balance visual quality with performance, readability, and maintainability.

## Required Workflow
1. Understand the user goal and target audience.
2. Inspect the existing app structure, styling system, routes, and relevant components.
3. Identify the minimal UI needed for the task and define layout, hierarchy, states, and interactions.
4. Choose a design pattern that matches the app's architecture and constraints.
5. Produce or update the necessary frontend code with accessibility and responsiveness in mind.
6. Validate that the UI works logically, is consistent, and does not introduce unnecessary complexity.
7. For a visual change, verify both the desktop and narrow-screen layout when a browser preview or existing UI test is available.

## Decision Framework

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

## Trading Dashboard Patterns
When designing a trading dashboard, organize the page around decisions and risk rather than decoration:
- Put account health, circuit-breaker state, open exposure, and daily P&L near the top.
- Separate market context, signal information, and order controls into distinct regions.
- Give each metric a label, unit, timestamp or period, and a clear unavailable state.
- Use tables for exact values and charts for trends; do not make a chart the only place where a critical value can be read.
- Make symbol, timeframe, position size, stop-loss, take-profit, and estimated risk visible before an order can be reviewed.
- Show stale, disconnected, or delayed market data as an explicit operational state.

## State Model
For every data-driven component, account for these states where applicable:
- **loading**: reserve space and identify what is being loaded
- **ready**: show the value, unit, time context, and relevant action
- **empty**: explain why there is no data and what the user can do next
- **stale**: identify the last update and avoid implying that the value is current
- **error**: explain the failed operation without masking the failure
- **blocked**: explain which safety rule or prerequisite prevents an action

## Order-Flow Safety
For order-entry or position-management UI:
- Validate required inputs and ranges before submission.
- Display the resolved symbol, direction, size, price context, and protective levels.
- Present estimated exposure and relevant risk limits before confirmation.
- Require an explicit confirmation for submission, cancellation, or emergency actions.
- Disable duplicate submission while a request is in progress.
- Report the server result and correlation/reference information after completion.
- The UI must not bypass backend authorization, symbol whitelists, magic-number checks, position limits, or daily-loss circuit breakers. A disabled control must explain why it is disabled when the reason is actionable.

## Visual Language
- Prefer a restrained palette with a neutral base and semantic status colors.
- Use positive/negative colors consistently, but include text labels or symbols.
- Reserve strong warning and danger treatments for actionable risk.
- Use consistent spacing, border, typography, and focus conventions from the existing UI.
- Do not introduce a new component library or design system when an existing one fits.

## Output Expectations
When asked to design or implement UI work, provide:
- A brief understanding of the product goal and user need
- The recommended page/component structure
- The key interactions and state transitions
- A concrete implementation approach
- Any trade-offs or assumptions
- Code when relevant, with small, focused changes

## Safety and Quality Rules
- Do not invent backend APIs or data contracts unless the user approves them.
- Do not hide critical actions or business logic behind unclear UI patterns.
- Do not over-engineer simple interfaces.
- Prefer using existing components and design patterns before creating new abstractions.
- For financial or high-risk flows, include clear validation, warnings, and fail-safe states.

## Example Prompts
- "Design a responsive trading dashboard for watchlists, orders, and P&L."
- "Create a clean login page with validation and accessible error states."
- "Build a settings screen with dark mode, form validation, and saved state."
- "Improve this table UI for readability, filtering, and mobile responsiveness."

## Implementation Notes for This Repo
This project is a trading system and should favor:
- Clear dashboards and data density controls
- Visible risk and status states
- Strong contrast and legibility for live metrics
- Simple but trustworthy order entry flows
- Caution messaging for high-risk actions
- Components that support monitoring and operational awareness

When working in this repo, keep the frontend aligned with the project's architecture and safety requirements, especially around financial actions and operational data.