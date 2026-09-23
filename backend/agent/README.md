# Guarded forecast operator

`ForecastOperator` uses the Responses API function-call loop. The model sees fixed
`as_of`, horizon, model ID, candidate snapshot IDs, artifact IDs and validation
summaries. It never receives forecast power values. `ForecastService` performs
numerical inference; `AgentTools.publish_forecast` rechecks temporal and data
invariants and writes `AGENT_TOOL_CALL` and `AGENT_DECISION` events to the run.
These events are available through the existing `/forecast-runs/{id}/events` API.

The run's `SUCCEEDED` status and `/forecast` points are produced by the existing
backend worker before the agent's separate publication approval. The operator
does not change that API contract. Consumers that require agent approval must
check the `publish_forecast` event outcome as well as the run status.

## Execution labels

- `live_openai`: Responses API returned actual function calls that completed the gate.
- `recorded`: tool calls were replayed from a saved trace; no OpenAI call occurred.
- `deterministic_fallback`: OpenAI did not complete; local code ran a bounded
  deterministic sequence. It is never reported as an OpenAI decision.

An injected weather failure is explicitly named in the trace and output. For a
recovery demo, put an unavailable candidate first and a registered, temporally
valid snapshot second. No synthetic weather values or February SCADA are needed.

## Live smoke

Register a real snapshot and a compatible model bundle in the server-controlled
directories. Set `OPENAI_API_KEY` only in the local process environment. Then run:

```text
python -m backend.agent.demo --database data/runtime/agent.sqlite3 \
  --snapshot-dir data/runtime/snapshots --model-dir data/runtime/models \
  --as-of 2026-02-06T00:00:00Z --horizon 48 --model-id MODEL_ID \
  --snapshot NEWEST_ID --snapshot PREVIOUS_ID --execution live --require-live
```

The command prints JSON containing the exact tool trace and engine forecast.
Use `--inject-unavailable NEWEST_ID` only for an explicitly labelled failure
demo, and `--service-fixture` if a model or input is a service fixture.
For offline replay of a saved JSON output, pass `--execution recorded
--trace-file PATH`. Exit code 2 with `--require-live` means real OpenAI publication
was not demonstrated. The default budget is 10 tool calls and 45 seconds.
