"""Opt-in agent smoke run against registered server inputs.

Example (fixture inputs must be labelled):
python -m backend.agent.demo --database work/agent.sqlite3 --snapshot-dir work/snapshots \
  --model-dir work/models --as-of 2026-02-06T00:00:00Z --horizon 48 \
  --model-id linear-v1 --snapshot fresh --snapshot previous --execution live \
  --inject-unavailable fresh --service-fixture
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.storage import Store
from backend.workflow import ForecastService

from .operator import ForecastOperator, OpenAIResponses
from .tools import RunContext


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the guarded forecast operator")
    parser.add_argument("--database", required=True)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--horizon", type=int, choices=(24, 48), required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--snapshot", action="append", required=True,
                        help="Registered candidate ID, newest first; repeat for fallback")
    parser.add_argument("--execution", choices=("live", "recorded"), required=True)
    parser.add_argument("--trace-file", help="JSON output from a prior run, required for recorded mode")
    parser.add_argument("--openai-model", default="gpt-4.1-mini")
    parser.add_argument("--inject-unavailable", action="append", default=[],
                        help="Explicit demo source failure; never use for operational runs")
    parser.add_argument("--service-fixture", action="store_true",
                        help="Label registered model/weather inputs as service fixtures")
    parser.add_argument("--require-live", action="store_true",
                        help="Exit nonzero unless real OpenAI tool calls publish")
    args = parser.parse_args()
    context = RunContext(args.as_of, args.horizon, args.model_id, tuple(args.snapshot))
    service = ForecastService(Store(args.database), args.snapshot_dir, args.model_dir)
    operator = ForecastOperator(service, context, OpenAIResponses(args.openai_model),
                                unavailable=frozenset(args.inject_unavailable))
    if args.execution == "live":
        result = operator.run_live()
    else:
        if not args.trace_file:
            parser.error("--trace-file is required for recorded mode")
        source = json.loads(Path(args.trace_file).read_text(encoding="utf-8"))
        calls = [(entry["tool"], entry["arguments"]) for entry in source["trace"]
                 if entry["type"] == "AGENT_TOOL_CALL"]
        result = operator.run_recorded(calls)
    payload = result.to_json()
    payload["service_fixture"] = args.service_fixture
    payload["injected_source_failure"] = args.inject_unavailable
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.require_live and (result.execution != "live_openai" or not result.published):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
