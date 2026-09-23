# White Wind Operator Dashboard Design

## Goal

Build one responsive React/TypeScript dashboard for replaying a 24 or 48 hour wind forecast, inspecting two turbine series, and reviewing the run audit and agent tool trace. The page must clearly distinguish demonstration fixtures from live API data.

## Visual direction

The dashboard uses a warm white background with a subtle grid, white bento cards, thin neutral borders, soft shadows, graphite text, and restrained emerald and turquoise accents. Motion is limited to short state transitions and disabled through `prefers-reduced-motion`.

At 1440 px the page uses a wide content column. The forecast chart occupies the main area, with the agent timeline beside it. Replay controls and provenance cards sit above; audit and export actions sit below. On a narrow screen every section becomes a single readable column without horizontal page scrolling.

## Components

- `ReplayControls` owns the replay timestamp including its explicit timezone offset, the 24/48 hour selector, data source selector, and run action.
- `ForecastChart` uses Recharts for separate Turbine 1 and Turbine 2 lines. It does not show actuals, confidence bands, or an aggregate unless those fields exist in a real response and are explicitly labelled.
- `AgentTimeline` lists real tool/event names, timestamps, and statuses from the selected run. Demo events remain visibly marked as fixture data.
- `AuditPanel` shows temporal validation, model identity, weather provenance, available source timestamps, warnings, snapshot/hash identifiers, and revision ancestry only when supplied.
- Small provenance cards summarize model version, weather age, SCADA freshness, and temporal validation. Missing values render as unavailable rather than invented numbers.

## Data boundary

`api.ts` exposes a typed adapter for create-run, status polling, forecast, audit, and events endpoints. `demoFixture.ts` implements the same dashboard data contract and contains deliberately labelled illustrative values. The UI selects demo or API mode explicitly; a failed API request produces an error state and never falls back silently to fixtures.

The adapter reads `VITE_API_BASE_URL`. Since the backend contract is still under parallel development, response normalization is isolated from visual components so endpoint payload changes stay local to the adapter.

## States and behavior

The page supports idle/empty, loading, success, and error states. A created API run is polled until a terminal status; timers and requests are cleaned up when the source changes or the component unmounts. Export buttons serialize only the currently displayed run as JSON or CSV and preserve its demo marker.

The initial source is demo so the standalone frontend is reviewable before API integration. A persistent `DEMO DATA` badge, explanatory copy, and fixture-prefixed run identifier prevent the values from being mistaken for measured forecasts.

## Verification

- Lint and production build complete successfully.
- The demo flow can be run and exported without a backend.
- API errors remain errors and do not display fixture forecasts.
- The layout and key controls are checked in a browser at 1440 px and 390 px.
- Text contrast, keyboard focus, chart legend, and reduced-motion behavior are checked manually.

## Scope

All changes stay under `frontend/`. The implementation does not add authentication, maps, 3D elements, marketing sections, backend changes, or fabricated operational metrics.
