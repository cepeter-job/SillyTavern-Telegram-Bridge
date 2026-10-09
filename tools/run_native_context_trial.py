#!/usr/bin/env python3
"""Explicit, resumable synthetic experiment. Never alter live bridge state/settings."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_native_context import digest, file_digest, revalidate_case  # noqa: E402
from tools.native_context_limits import TrialBudget  # noqa: E402
from tools.native_context_review import REVIEW_SYSTEM, make_review_messages, parse_review  # noqa: E402
from tools.native_context_transport import SubscriptionClient, configured_client  # noqa: E402
from tools.native_context_trial import make_schedule, summarize_trial  # noqa: E402

PROTOCOL_FILES = (
    "bridge/context_history_codec.py",
    "bridge/context_native_receipt.py",
    "tools/evaluate_native_context.py",
    "tools/native_context_fixture.py",
    "tools/native_context_limits.py",
    "tools/native_context_review.py",
    "tools/native_context_transport.py",
    "tools/native_context_trial.py",
    "tools/run_native_context_trial.py",
)


def protocol_digest() -> str:
    return digest({name: file_digest(ROOT / name) for name in PROTOCOL_FILES})


def freeze_schedule(plan: dict) -> dict:
    """Fix order, rubric and implementation before observing any provider result."""
    result = copy.deepcopy(plan)
    if result.pop("plan_sha256", None) != digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("native_plan_digest_invalid")
    if "schedule" in result:
        raise ValueError("native_plan_already_frozen")
    seed = int(result["workload_sha256"][:16], 16)
    result["schedule"] = make_schedule([case["case_id"] for case in result["cases"]], seed=seed)
    result["review_protocol_sha256"] = digest(REVIEW_SYSTEM)
    result["implementation_sha256"] = protocol_digest()
    result["review_limits"] = {"maximum_output_tokens": 1000, "temperature": 0.0}
    result["plan_sha256"] = digest(result)
    return result


def _check_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or plan.get("plan_sha256") != digest(
        {k: v for k, v in plan.items() if k != "plan_sha256"}
    ):
        raise ValueError("native_plan_digest_invalid")
    if plan.get("implementation_sha256") != protocol_digest() or plan.get("review_protocol_sha256") != digest(
        REVIEW_SYSTEM
    ):
        raise ValueError("native_trial_protocol_changed")
    if plan.get("production_activation_allowed") is not False or plan.get("network") != {"requests": 0}:
        raise ValueError("native_trial_is_not_offline_prepared")
    if plan.get("model") != "nano-gpt::z-ai/glm-5.2":
        raise ValueError("native_trial_model_not_predeclared")
    wanted = make_schedule([c["case_id"] for c in plan["cases"]], seed=int(plan["workload_sha256"][:16], 16))
    if plan.get("schedule") != wanted or len(wanted) > 24:
        raise ValueError("native_trial_schedule_changed")
    budget = plan.get("budget", {})
    bounds = {
        "maximum_requests": 24,
        "maximum_logical_input_tokens": 300000,
        "maximum_output_tokens": 24000,
        "maximum_request_bytes": 150000,
    }
    if any(type(budget.get(k)) is not int or not 0 < budget[k] <= v for k, v in bounds.items()):
        raise ValueError("native_trial_budget_invalid")
    if budget.get("paid_overage") is not False or budget.get("minimum_subscription_reserve") != 100000:
        raise ValueError("native_trial_subscription_policy_changed")
    if plan.get("review_limits") != {"maximum_output_tokens": 1000, "temperature": 0.0}:
        raise ValueError("native_trial_review_limits_changed")
    for case in plan["cases"]:
        if case.get("native_source_kind") != "synthetic_story_accepted_by_native_sqlite_pipeline":
            raise ValueError("native_trial_rejects_production_transcripts")
        pair = case.get("variants", {})
        if set(pair) != {"baseline", "candidate"}:
            raise ValueError("native_trial_pair_incomplete")
        left, right = pair["baseline"], pair["candidate"]
        if left["model"] != plan["model"] or right["model"] != plan["model"] or left["settings"] != right["settings"]:
            raise ValueError("native_trial_unmatched_settings")
        settings = left["settings"]
        if (
            set(settings) != {"max_tokens", "temperature"}
            or type(settings["max_tokens"]) is not int
            or not 256 <= settings["max_tokens"] <= 1000
        ):
            raise ValueError("native_trial_output_budget_changed")


def _sealed(value: dict, key: str) -> dict:
    result = {k: v for k, v in value.items() if k != key}
    result[key] = digest(result)
    return result


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError("native_trial_symlink_output")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _save(path: Path, state: dict, client: SubscriptionClient) -> None:
    state["budget"] = asdict(client.budget)
    state["quota_remaining_at_last_admission"] = client.last_quota
    state.update(_sealed(state, "state_sha256"))
    _write(path, state)


def _lock_digest(attempts: list[dict]) -> str:
    return digest(
        [
            {"id": item["attempt_id"], "request": item["request_sha256"], "judgment": item["judgment"]}
            for item in attempts
            if item["kind"] == "review"
        ]
    )


def validate_trial_state(plan: dict, state: dict) -> None:
    _check_plan(plan)
    if state.get("plan_sha256") != plan["plan_sha256"] or state != _sealed(state, "state_sha256"):
        raise ValueError("native_trial_state_changed")
    rows = state.get("attempts")
    if not isinstance(rows, list) or len(rows) > len(plan["schedule"]):
        raise ValueError("native_trial_state_shape_invalid")
    for row, planned in zip(rows, plan["schedule"][: len(rows)], strict=True):
        if any(row.get(k) != v for k, v in planned.items()) or row != _sealed(row, "attempt_sha256"):
            raise ValueError("native_trial_attempt_changed")
        if row.get("status") not in {"pending", "failed", "complete"}:
            raise ValueError("native_trial_attempt_status_invalid")
    if state.get("budget", {}).get("requests") != len(rows):
        raise ValueError("native_trial_attempt_count_mismatch")
    if state.get("production_activation_allowed") is not False:
        raise ValueError("native_trial_cannot_activate")
    if state.get("reviews_locked") is True:
        if len(rows) != len(plan["schedule"]) or any(row["status"] != "complete" for row in rows):
            raise ValueError("native_trial_premature_review_lock")
        if state.get("review_lock_sha256") != _lock_digest(rows):
            raise ValueError("native_trial_review_lock_changed")


def _body(plan: dict, planned: dict, attempts: list[dict]) -> tuple[dict, dict]:
    case = next(c for c in plan["cases"] if c["case_id"] == planned["case_id"])
    if planned["kind"] == "generation":
        variant = case["variants"][planned["variant"]]
        messages, settings = variant["messages"], variant["settings"]
        extra = {}
    else:
        generations = {
            a["variant"]: a["output"]
            for a in attempts
            if a["case_id"] == case["case_id"] and a["kind"] == "generation" and a["status"] == "complete"
        }
        if set(generations) != {"baseline", "candidate"}:
            raise ValueError("native_review_requires_matched_outputs")
        messages = make_review_messages(case, *(generations[label] for label in planned["order"]))
        settings = {"max_tokens": 1000, "temperature": 0.0}
        extra = {"response_format": {"type": "json_object"}}
    body = {"model": plan["model"].split("::", 1)[1], "messages": messages, **settings, "stream": False, **extra}
    return case, body


def execute_trial(
    directory: Path,
    plan: dict,
    client: SubscriptionClient,
    state_path: Path,
    *,
    max_steps: int = 24,
) -> dict:
    """Persist a pending receipt before each send; ambiguous delivery is never retried."""
    import fcntl

    _check_plan(plan)
    if type(max_steps) is not int or not 1 <= max_steps <= 24:
        raise ValueError("native_trial_steps_invalid")
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.is_symlink():
        raise ValueError("native_trial_symlink_state")
    with state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if state_path.exists():
            state = json.loads(state_path.read_text())
            validate_trial_state(plan, state)
            if any(row["status"] != "complete" for row in state["attempts"]):
                raise ValueError("native_trial_pending_or_failed_attempt_no_auto_retry")
            if asdict(client.budget) != state["budget"]:
                raise ValueError("native_trial_budget_resume_mismatch")
        else:
            if client.budget.requests:
                raise ValueError("native_trial_client_already_used")
            state = {
                "schema_version": 1,
                "plan_sha256": plan["plan_sha256"],
                "attempts": [],
                "reviews_locked": False,
                "production_activation_allowed": False,
            }
        pending = plan["schedule"][len(state["attempts"]) :][:max_steps]
        for planned in pending:
            case, body = _body(plan, planned, state["attempts"])
            revalidate_case(directory, case)
            encoded = client.admit(body)
            row = {
                **planned,
                "status": "pending",
                "request_sha256": digest(body),
                "request_bytes": len(encoded),
                "output_cap": body["max_tokens"],
            }
            row.update(_sealed(row, "attempt_sha256"))
            state["attempts"].append(row)
            _save(state_path, state, client)
            try:
                returned = client.send_reserved(encoded)
                row.update(returned)
                if planned["kind"] == "review":
                    row["judgment"] = parse_review(returned["output"])
                revalidate_case(directory, case)
                row["status"] = "complete"
            except Exception as error:
                row.update(status="failed", error_class=type(error).__name__)
                if client.last_usage is not None:
                    row["usage"] = client.last_usage
            row.update(_sealed(row, "attempt_sha256"))
            _save(state_path, state, client)
            print(
                json.dumps(
                    {
                        "completed_attempts": len(state["attempts"]),
                        "phase": planned["kind"],
                        "status": row["status"],
                        "input_tokens": client.budget.input_tokens,
                        "output_tokens": client.budget.output_tokens,
                    }
                ),
                flush=True,
            )
            if row["status"] != "complete":
                break
        if len(state["attempts"]) == len(plan["schedule"]) and all(
            a["status"] == "complete" for a in state["attempts"]
        ):
            state["reviews_locked"] = True
            state["review_lock_sha256"] = _lock_digest(state["attempts"])
        _save(state_path, state, client)
        validate_trial_state(plan, state)
        return state


def export_evidence(directory: Path, plan: dict, state: dict) -> dict:
    """Keep the human-review packet separate from variant keys and measurements."""
    report = summarize_trial(plan, state)
    _write(directory / "report.json", report)
    if state.get("reviews_locked") is True:
        human = {
            "instructions": "Review only this file before looking at the unmasking key or report. No human "
            "approval is recorded.",
            "reviewer_name": None,
            "human_approved": False,
            "cases": [],
        }
        key = {}
        for case in plan["cases"]:
            review = next(a for a in state["attempts"] if a["kind"] == "review" and a["case_id"] == case["case_id"])
            outputs = {
                a["variant"]: a["output"]
                for a in state["attempts"]
                if a["kind"] == "generation" and a["case_id"] == case["case_id"]
            }
            human["cases"].append(
                {
                    "case_id": case["case_id"],
                    "task": case["request"],
                    "canon": case["review_canon"],
                    "language": case["language"],
                    "A": outputs[review["order"][0]],
                    "B": outputs[review["order"][1]],
                    "scores_A": None,
                    "scores_B": None,
                    "critical_errors": None,
                }
            )
            key[case["case_id"]] = dict(zip(("A", "B"), review["order"], strict=True))
        _write(directory / "human_blinded_review.json", human)
        _write(directory / "unmasking_key.json", {"review_lock_sha256": state["review_lock_sha256"], "key": key})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--steps", type=int, default=24)
    args = parser.parse_args()
    directory = args.directory.resolve()
    trial_path = directory / "trial-plan.json"
    if args.freeze:
        if trial_path.exists() or args.execute:
            parser.error("frozen plan exists or freeze/execute combined")
        frozen = freeze_schedule(json.loads((directory / "plan.json").read_text()))
        _write(trial_path, frozen)
        print(
            json.dumps(
                {
                    "plan_sha256": frozen["plan_sha256"],
                    "expected_requests": len(frozen["schedule"]),
                    "network_requests": 0,
                }
            )
        )
        return 0
    plan = json.loads(trial_path.read_text())
    _check_plan(plan)
    if not args.execute:
        for case in plan["cases"]:
            revalidate_case(directory, case)
        print(
            json.dumps(
                {"native_cases_verified": len(plan["cases"]), "plan_sha256": plan["plan_sha256"], "network_requests": 0}
            )
        )
        return 0
    if args.expected_plan_sha256 != plan["plan_sha256"]:
        parser.error("execution requires the precommitted exact plan hash")
    path = directory / "state.json"
    if path.exists():
        saved = json.loads(path.read_text())
        validate_trial_state(plan, saved)
        budget = TrialBudget(**saved["budget"])
    else:
        limits = plan["budget"]
        budget = TrialBudget(
            maximum_requests=limits["maximum_requests"],
            maximum_input_tokens=limits["maximum_logical_input_tokens"],
            maximum_output_tokens=limits["maximum_output_tokens"],
            maximum_request_bytes=limits["maximum_request_bytes"],
        )
    client = configured_client(plan["model"], budget)
    state = execute_trial(directory, plan, client, path, max_steps=args.steps)
    report = export_evidence(directory, plan, state)
    print(
        json.dumps(
            {
                "requests": report["accounting"]["physical_requests"],
                "complete": report["accounting"]["complete"],
                "measured_reduction": report["measured"]["story_input_reduction_fraction"],
                "automated_review_passed": report["automated_review_passed"],
                "production_activation_allowed": False,
            }
        )
    )
    return 0 if report["accounting"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
