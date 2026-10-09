#!/usr/bin/env python3
"""Execute only a pre-frozen, bounded subscription-only synthetic trial."""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.native_context_limits import TrialBudget  # noqa: E402
from tools.native_context_transport import BASE, configured_client  # noqa: E402
from tools.postrelease_plan import AXES, PROFILES, digest, judge_body, make_plan, schedule  # noqa: E402


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_state(path: Path, plan_digest: str) -> dict:
    if not path.exists():
        return {"plan_sha256": plan_digest, "records": [], "budgets": {}, "pending": None, "stopped": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("plan_sha256") != plan_digest or state.get("pending") is not None or state.get("stopped"):
        raise ValueError("ambiguous_stopped_or_changed_trial_cannot_resume")
    return state


def validate_judgment(text: str) -> dict:
    value = json.loads(text)
    if (
        not isinstance(value, dict)
        or set(value) != {"A", "B", "preference"}
        or value["preference"] not in {"A", "B", "tie"}
    ):
        raise ValueError("judge_shape_invalid")
    for label in ("A", "B"):
        item = value[label]
        if not isinstance(item, dict) or set(item) != {"hard_failures", "scores", "reason", "evidence"}:
            raise ValueError("judge_item_invalid")
        if not isinstance(item["hard_failures"], list) or not all(isinstance(v, str) for v in item["hard_failures"]):
            raise ValueError("judge_hard_failures_invalid")
        if not isinstance(item["scores"], dict) or set(item["scores"]) != set(AXES):
            raise ValueError("judge_score_axes_invalid")
        if not all(type(v) is int and 1 <= v <= 5 for v in item["scores"].values()):
            raise ValueError("judge_score_range_invalid")
        if not isinstance(item["reason"], str) or not isinstance(item["evidence"], str):
            raise ValueError("judge_reason_invalid")
    return value


def execute(plan_path: Path, destination: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_digest = digest(plan)
    for name, expected in plan["code_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen_code_changed:" + name)
    if plan["model_post_requests_ceiling"] != 152 or plan["production_activation_allowed"] is not False:
        raise ValueError("unapproved_protocol_limits")
    trials = plan.get("trials", [])
    if (
        [trial.get("id") for trial in trials] != [*PROFILES, "hybrid_history"]
        or [len(trial["cases"]) for trial in trials] != [8, 8, 8, 8, 6]
        or sum(len(trial["schedule"]) for trial in trials) != 152
        or any(trial["schedule"] != schedule(len(trial["cases"])) for trial in trials)
        or plan.get("total_input_ceiling") != 3000000
        or plan.get("total_output_ceiling") != 152000
        or plan.get("minimum_initial_quota", 0) < 3100000
    ):
        raise ValueError("unapproved_inventory_or_total_budget")
    destination.mkdir(parents=True, exist_ok=True)
    state_path = destination / "state.json"
    with (destination / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_state(state_path, plan_digest)
        admission = configured_client(
            plan["model_selection"], TrialBudget(maximum_requests=16, maximum_output_tokens=16000)
        )
        remaining = admission.quota(1)
        if remaining < plan["minimum_initial_quota"]:
            raise ValueError("whole_trial_allowance_insufficient")
        # This GET performs no generation and never exports the credential.
        req = urllib.request.Request(  # noqa: S310 -- fixed subscription HTTPS host through DNS-pinned opener
            BASE + "/models", headers={"x-api-key": admission._key, "Accept": "application/json"}
        )
        with admission._open(req, timeout=25, environ=admission._environ) as response:
            raw = response.read(2000000)
        catalog = json.loads(raw)
        entries = catalog.get("data", catalog.get("models", []))
        if not any(isinstance(v, dict) and v.get("id") == admission.model for v in entries):
            raise ValueError("model_not_in_subscription_catalogue")
        state["subscription_preflight"] = {
            "active": True,
            "allow_overage": False,
            "remaining_input_tokens": remaining,
            "selected_model_included": True,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        atomic_json(state_path, state)
        print("PREFLIGHT_OK", "remaining", remaining, "model_posts_ceiling", 152, flush=True)
        clients = {}
        ordinal = 0
        for trial in plan["trials"]:
            for position, job in enumerate(trial["schedule"]):
                identity = f"{trial['id']}/{position:03d}"
                if ordinal < len(state["records"]):
                    if state["records"][ordinal]["id"] != identity:
                        raise ValueError("record_inventory_mismatch")
                    ordinal += 1
                    continue
                case = trial["cases"][job["case"]]
                if job["kind"] == "story":
                    body = case[job["variant"]]
                else:
                    outputs = {
                        record["variant"]: record["response"]["output"]
                        for record in state["records"]
                        if record["trial"] == trial["id"]
                        and record["case"] == case["id"]
                        and record["kind"] == "story"
                        and record["status"] == "completed"
                    }
                    if set(outputs) != {"baseline", "candidate"}:
                        raise ValueError("judge_requires_complete_matched_outputs")
                    body = judge_body(case, outputs, job["swap"])
                block = f"{trial['id']}/{position // 16}"
                if block not in clients:
                    budget = (
                        TrialBudget(**state["budgets"][block])
                        if block in state["budgets"]
                        else TrialBudget(maximum_requests=16, maximum_output_tokens=16000)
                    )
                    clients[block] = configured_client(plan["model_selection"], budget)
                client = clients[block]
                record = {
                    "id": identity,
                    "ordinal": ordinal,
                    "trial": trial["id"],
                    "case": case["id"],
                    **{key: value for key, value in job.items() if key != "case"},
                    "body_sha256": digest(body),
                    "budget_block": block,
                }
                started = time.monotonic()
                try:
                    encoded = client.admit(body)
                    atomic_json(destination / "requests" / f"{ordinal:03d}.json", body)
                    state["pending"] = record
                    state["budgets"][block] = asdict(client.budget)
                    atomic_json(state_path, state)
                    response = client.send_reserved(encoded)
                    record.update(
                        status="completed", response=response, latency_seconds=round(time.monotonic() - started, 3)
                    )
                    if job["kind"] == "judge":
                        try:
                            judgment = validate_judgment(response["output"])
                            labels = ("candidate", "baseline") if job["swap"] else ("baseline", "candidate")
                            record["judge_evidence_quotes_valid"] = all(
                                not judgment[label]["evidence"] or judgment[label]["evidence"] in outputs[labels[index]]
                                for index, label in enumerate(("A", "B"))
                            )
                            record["judgment"] = judgment
                        except (ValueError, TypeError, KeyError):
                            # Preserve invalid automated judgments, never repair or selectively rerun.
                            record["judge_invalid"] = True
                except Exception as exc:
                    record.update(
                        status="failed",
                        error_class=type(exc).__name__,
                        usage=client.last_usage,
                        unknown_usage=client.budget.unknown_usage,
                        latency_seconds=round(time.monotonic() - started, 3),
                    )
                    state["stopped"] = True
                state["pending"] = None
                state["records"].append(record)
                state["budgets"][block] = asdict(client.budget)
                state["provider_model_posts"] = sum(v["requests"] for v in state["budgets"].values())
                atomic_json(state_path, state)
                print(
                    "REQUEST",
                    ordinal + 1,
                    "/152",
                    trial["id"],
                    case["id"],
                    job["kind"],
                    record["status"],
                    "input",
                    client.budget.input_tokens,
                    flush=True,
                )
                if state["stopped"]:
                    raise ValueError("trial_stopped_with_preserved_evidence")
                ordinal += 1
        state["completed"] = True
        state["human_review_approved"] = False
        state["production_activation_allowed"] = False
        atomic_json(state_path, state)
        print("ALL_FROZEN_TRIAL_REQUESTS_FINISHED", ordinal, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.freeze == args.execute:
        parser.error("choose exactly one of --freeze or --execute")
    if args.freeze:
        if args.plan.exists():
            parser.error("never overwrite a frozen plan")
        plan = make_plan()
        atomic_json(args.plan, plan)
        print(
            json.dumps(
                {
                    "plan_sha256": digest(plan),
                    "model_posts_ceiling": plan["model_post_requests_ceiling"],
                    "trials": [t["id"] for t in plan["trials"]],
                    "provider_generation_requests": 0,
                }
            )
        )
    else:
        if args.output is None:
            parser.error("execution requires --output")
        execute(args.plan, args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("POSTRELEASE_STOPPED", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
