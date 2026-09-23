# White Wind Operator Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one responsive white dashboard for replay controls, two-turbine forecast visualization, audit evidence, and agent timeline with explicit demo fixtures and a replaceable API adapter.

**Architecture:** A small Vite React application owns the orchestration state in `App.tsx`. Typed dashboard models sit in `types.ts`; `api.ts` and `demoFixture.ts` implement the live and demo data boundaries, while focused components render controls, chart, timeline, and audit without knowing transport details.

**Tech Stack:** React 19, TypeScript, Vite, Recharts, CSS, Vitest, Testing Library

---

### Task 1: Scaffold and typed data adapters

**Files:**
- Create: `frontend/package.json`, `frontend/package-lock.json`, `frontend/index.html`, `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json`, `frontend/vite.config.ts`, `frontend/eslint.config.js`, `frontend/.gitignore`
- Create: `frontend/src/main.tsx`, `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/demoFixture.ts`
- Test: `frontend/src/api.test.ts`

- [ ] Scaffold Vite React TypeScript with scripts `dev`, `build`, `lint`, and `test`; install React, Recharts, Vite, ESLint, Vitest, jsdom, and Testing Library.
- [ ] Define run, forecast point, audit, event, dashboard, source, and terminal-status types. Optional provenance remains optional so missing data renders unavailable.
- [ ] Implement `createApiAdapter(baseUrl)` with `createRun`, `getRun`, `getForecast`, `getAudit`, and `getEvents`; every non-OK response throws an error containing endpoint context.
- [ ] Implement an explicit demo adapter returning a fixture-prefixed run with `isDemo: true`, two illustrative turbine series, audit provenance, warnings, and agent events.
- [ ] Test that API errors throw and never resolve demo content; test that the demo adapter labels the result as demo.
- [ ] Run `npm --prefix frontend test -- --run` and expect all adapter tests to pass.

### Task 2: Build dashboard components and states

**Files:**
- Create: `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/styles.css`
- Create: `frontend/src/components/ReplayControls.tsx`, `frontend/src/components/ForecastChart.tsx`, `frontend/src/components/AgentTimeline.tsx`, `frontend/src/components/AuditPanel.tsx`

- [ ] Build the page shell with `Wind Operator`, backend/agent status chips, persistent demo disclosure, replay controls, four provenance summaries, forecast chart, agent timeline, and audit/export area.
- [ ] Use Recharts for two visually distinct turbine lines, normalized power axis, responsive sizing, accessible legend, and explicit copy that actuals/confidence are unavailable.
- [ ] Implement explicit Demo/API source selection. Demo is the initial source; API errors show an error state without fixture fallback. Support idle, loading/polling, success, and error states with retry.
- [ ] Poll nonterminal API runs and clear timers on source change/unmount. Download JSON and CSV for the visible run, including the demo marker.
- [ ] Add tests for the visible demo disclosure, both turbine legends, loading and error states, source selection, and absence of fabricated actual/confidence content.
- [ ] Run `npm --prefix frontend test -- --run` and expect all component tests to pass.

### Task 3: Responsive finish and verification

**Files:**
- Modify: `frontend/src/styles.css`
- Modify as required: `frontend/src/App.tsx`, `frontend/src/components/*.tsx`

- [ ] Finish the Aceternity-inspired white bento styling: warm white grid background, white cards, subtle borders/shadows, graphite type, emerald/teal accents, visible focus rings, and no nested-card clutter.
- [ ] Add breakpoints so the 1440 px grid and 390 px single-column layout have no page overflow; keep controls usable and chart readable.
- [ ] Respect `prefers-reduced-motion` and avoid continuous decorative animation.
- [ ] Run `npm --prefix frontend run lint`, `npm --prefix frontend test -- --run`, and `npm --prefix frontend run build`; all must exit 0.
- [ ] Run the Vite server and inspect screenshots at 1440×1000 and 390×844. Fix clipping, overlap, contrast, unclear demo labels, or inaccessible controls.
- [ ] Confirm `git diff --name-only main...HEAD` contains only `frontend/` paths.
