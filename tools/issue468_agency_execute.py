#!/usr/bin/env python3
"""Freeze, execute, or report one finite paired agency experiment; no story writes."""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.issue468_agency_trial import (  # noqa: E402
    BASE_COMMIT,
    MODEL,
    PLAN_CASE_COUNT,
    digest,
    make_plan,
    render_human_review,
    scorecard_csv,
    validate_pair,
    verify_frozen_sources,
)
from tools.native_context_limits import TrialBudget  # noqa: E402
from tools.native_context_transport import BASE, configured_client  # noqa: E402


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        os.chmod(path, 0o600)
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as file:
        os.chmod(tmp, 0o600)
        json.dump(obj, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)


def freeze(path: Path) -> dict:
    if path.exists():
        raise ValueError("never_overwrite_frozen_plan")
    plan = make_plan()
    if subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip() != BASE_COMMIT:
        raise ValueError("wrong_base_code_commit_before_freeze")
    verify_frozen_sources(plan)
    write_new(path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return {"frozen": True, "plan_sha256": digest(plan), "cases": len(plan["cases"]), "provider_posts": 32}


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        plan.get("provider_request_ceiling") != 32
        or len(plan.get("cases", [])) != PLAN_CASE_COUNT
        or len(plan.get("schedule", [])) != 32
        or plan.get("model_selection") != "nano-gpt::" + MODEL
        or plan.get("minimum_initial_subscription_allowance") != 250000
        or plan.get("paid_overage") is not False
        or plan.get("retry_or_repair_or_fallback") is not False
        or plan.get("production_activation_allowed") is not False
        or plan.get("maximum_parallel_requests") != 1
        or plan.get("maximum_input_tokens") != 600000
        or plan.get("maximum_output_tokens") != 32000
        or plan.get("runtime_commit") != BASE_COMMIT
    ):
        raise ValueError("unapproved_agency_protocol")
    for case in plan["cases"]:
        validate_pair(case)
    wanted = []
    for index in range(PLAN_CASE_COUNT):
        for variant in ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"):
            wanted.append({"case": index, "variant": variant})
    if plan["schedule"] != wanted or [c["id"] for c in plan["cases"]] != [c["id"] for c in make_plan()["cases"]]:
        raise ValueError("unapproved_agency_schedule")
    verify_frozen_sources(plan)
    return plan


def included_preflight() -> tuple[object, int]:
    client = configured_client("nano-gpt::" + MODEL, TrialBudget(maximum_requests=16, maximum_output_tokens=16000))
    remaining = client.quota(1)
    req = urllib.request.Request(  # noqa: S310 - fixed HTTPS subscription host and DNS-pinned opener
        BASE + "/models", headers={"x-api-key": client._key, "Accept": "application/json"}, method="GET"
    )
    with client._open(req, timeout=25, environ=client._environ) as response:
        data = json.loads(response.read(2_000_000))
    records = data.get("data", data.get("models", []))
    if not any(item.get("id") == MODEL for item in records if isinstance(item, dict)):
        raise ValueError("selected_model_not_in_active_subscription")
    return client, remaining


def execute(plan_path: Path, folder: Path) -> None:
    plan = load_plan(plan_path)
    if subprocess.check_output(["/usr/bin/git", "rev-parse", BASE_COMMIT], cwd=ROOT).decode().strip() != BASE_COMMIT:  # noqa: S603
        raise ValueError("frozen_base_not_locally_verifiable")
    if folder.exists() and (folder / "state.json").exists():
        raise ValueError("existing_trial_state_refuses_any_replay")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "execution.lock").open("x") as lock:
        os.chmod(folder / "execution.lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, remaining = included_preflight()
        if remaining < plan["minimum_initial_subscription_allowance"]:
            raise ValueError("whole_trial_subscription_allowance_insufficient")
        state: dict = {
            "protocol_digest": digest(plan),
            "started_at": now(),
            "complete": False,
            "stopped": False,
            "pending": None,
            "records": [],
            "budgets": {},
            "subscription_preflight": {
                "active": True,
                "allow_overage": False,
                "model_in_catalog": True,
                "allowance_gate_at_least_250000": True,
            },
            "independent_human_review_completed": False,
        }
        atomic_json(folder / "state.json", state)
        clients = {}
        for ordinal, job in enumerate(plan["schedule"]):
            block = ordinal // 16
            block_id = str(block)
            if block_id not in clients:
                clients[block_id] = configured_client(
                    plan["model_selection"], TrialBudget(maximum_requests=16, maximum_output_tokens=16000)
                )
            client = clients[block_id]
            case = plan["cases"][job["case"]]
            body = case[job["variant"]]
            record = {
                "ordinal": ordinal,
                "case": case["id"],
                "scenario_class": case["scenario_class"],
                "variant": job["variant"],
                "body_sha256": digest(body),
                "model": MODEL,
                "budget_block": block_id,
                "attempted_at": now(),
            }
            started = time.monotonic()
            try:
                encoded = client.admit(body)
                state["pending"] = record
                state["budgets"][block_id] = asdict(client.budget)
                atomic_json(folder / "state.json", state)
                response = client.send_reserved(encoded)
                record["status"] = "completed"
                record["response"] = response
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                state["records"].append(record)
                state["pending"] = None
                state["budgets"][block_id] = asdict(client.budget)
                atomic_json(folder / "state.json", state)
                print(
                    "PAIRED_PROVIDER_PROGRESS", ordinal + 1, "of", len(plan["schedule"]), "status=completed", flush=True
                )
            except Exception as exc:
                record["status"] = "failed"
                record["error_type"] = type(exc).__name__
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                record["usage"] = client.last_usage
                record["usage_may_be_unknown"] = client.budget.unknown_usage
                state["pending"] = None if client.last_usage is not None else record
                state["budgets"][block_id] = asdict(client.budget)
                state["records"].append(record)
                state["stopped"] = True
                atomic_json(folder / "state.json", state)
                print("PROVIDER_STOPPED_WITHOUT_RETRY", ordinal, type(exc).__name__, flush=True)
                raise RuntimeError("agency_trial_stopped_no_replay") from None
        state["complete"] = True
        state["finished_at"] = now()
        atomic_json(folder / "state.json", state)
        print("PAIRED_PROVIDER_COMPLETED", len(state["records"]), flush=True)


def render_packet_markdown(packet: dict) -> str:
    lines = [
        "# Issue #468 — Independent human adjudication packet",
        "",
        "16 fresh paired synthetic continuations. Outputs are blinded and randomized; "
        "the assignment map, model usage and prior reviews are not in this packet.",
        "",
        "A second human reviewer who has not seen the implementation or the prior grades should "
        "read each scene and mark each A/B response independently. Record Yes, No, or Unclear "
        "for invented user speech, added user action, knowledge leakage, causal/branch contradiction "
        "and transport format. For any Yes quote the exact erroneous span. "
        "Do not infer that these responses were human-approved; all scorecard cells are blank.",
        "",
    ]
    for n, item in enumerate(packet["pairs"], 1):
        lines += [
            f"## Pair {n:02d} — {item['id']}",
            "",
            "**Character/scenario:**",
            "",
            f"- Character: {item['card'].get('name', '')}",
            f"- Description: {item['card'].get('description', '')}",
            f"- Scenario: {item['card'].get('scenario', '')}",
            f"- Language: {item['language']}",
            "",
            "**Accepted story history:**",
            "",
        ]
        for turn in item["history"]:
            lines.append(f"- **{turn['role']}**: {turn['content']}")
        lines += [
            "",
            f"**Current user message:** {item['current_user_text']}",
            "",
            "**Required established facts:**",
            "",
        ]
        lines += [f"- {v}" for v in item["required_facts"]]
        lines += ["", "**Forbidden inferences:**", ""]
        lines += [f"- {v}" for v in item["forbidden_inferences"]]
        lines += [
            "",
            f"**Review focus:** {item['focus']}",
            "",
            "**Output A:**",
            "",
            item["A"],
            "",
            "**Output B:**",
            "",
            item["B"],
            "",
        ]
    return "\n".join(lines) + "\n"


def report(plan_path: Path, state_path: Path, out: Path, private: Path) -> dict:
    plan = load_plan(plan_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        state.get("protocol_digest") != digest(plan)
        or state.get("pending") is not None
        or state.get("stopped")
        or not state.get("complete")
        or len(state.get("records", [])) != 32
        or sum(block["requests"] for block in state["budgets"].values()) != 32
        or any(block["unknown_usage"] for block in state["budgets"].values())
    ):
        raise ValueError("incomplete_or_ambiguous_usage_not_reportable")
    output = {}
    totals = {"baseline": {"input": 0, "output": 0}, "candidate": {"input": 0, "output": 0}}
    for ordinal, record in enumerate(state["records"]):
        job = plan["schedule"][ordinal]
        case = plan["cases"][job["case"]]
        actual_usage = record["response"].get("usage")
        if (
            record["status"] != "completed"
            or record["ordinal"] != ordinal
            or record["case"] != case["id"]
            or record["variant"] != job["variant"]
            or record["body_sha256"] != digest(case[job["variant"]])
            or not actual_usage
            or actual_usage.get("complete") is not True
        ):
            raise ValueError("incomplete_or_changed_provider_observation")
        variant = job["variant"]
        output.setdefault(case["id"], {})[variant] = record["response"]
        totals[variant]["input"] += actual_usage["input_tokens"]
        totals[variant]["output"] += actual_usage["output_tokens"]
    packet, sheet, mapping = render_human_review(plan, output)
    if out.exists() or private.exists():
        raise ValueError("never_overwrite_blinded_human_packet")
    out.mkdir(parents=True)
    private.mkdir(parents=True, mode=0o700)
    write_new(out / "HUMAN_REVIEW.md", render_packet_markdown(packet))
    write_new(out / "HUMAN_REVIEW.json", json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    write_new(out / "HUMAN_SCORECARD.csv", scorecard_csv(sheet))
    atomic_json(private / "unblinding-map.json", mapping)
    input_total = totals["baseline"]["input"] + totals["candidate"]["input"]
    output_total = totals["baseline"]["output"] + totals["candidate"]["output"]
    summary = {
        "count_of_matched_scenarios": len(plan["cases"]),
        "physical_model_requests": len(state["records"]),
        "model": MODEL,
        "baseline_input_tokens": totals["baseline"]["input"],
        "candidate_input_tokens": totals["candidate"]["input"],
        "all_input_tokens": input_total,
        "all_output_tokens": output_total,
        "failed_requests": 0,
        "unknown_logical_usage": False,
        "paid_overage": False,
        "fallback_or_repair_or_retry": False,
        "independent_human_adjudication": "pending_external_human",
        "production_approval": False,
        "potential_effectiveness": "unproven_until_independent_review",
        "baseline_source": plan["old_policy_commit"],
        "candidate_source": plan["new_policy_commit"],
        "frozen_runtime_source": plan["runtime_commit"],
        "frozen_plan_digest": digest(plan),
        "blinded_pair_map_not_published": True,
    }
    atomic_json(out / "RESULTS.json", summary)
    markdown = [
        "# Issue #468 — Fresh matched NanoGPT agency study",
        "",
        "This is a fresh **16 paired-case / 32 physical generation request** synthetic test, "
        "not a production rollout or a human-quality approval.",
        "",
        f"- Matched model: NanoGPT included subscription {MODEL}.",
        "- Identical native card, history, user input and sampling for each pair; "
        "the **only** prompt difference is the added user-agency clarification from PR #479.",
        "- Temperature 0.7, maximum 800 output tokens and common 140-word narrative scope.",
        "- No extra generation, humanizer, language rewrite, fallback, retry, or repair call.",
        "- Provider request accounting: **32/32 completed**, 0 failed, 0 unknown logical usage.",
        f"- Provider input tokens: baseline **{totals['baseline']['input']:,}**, "
        f"candidate **{totals['candidate']['input']:,}**, total **{input_total:,}**.",
        f"- Total provider output tokens: **{output_total:,}**.",
        "- Scenarios: 10 unquoted conversational acts, 3 verbatim-quoted controls, "
        "3 no-speech/steering/physical controls.",
        "- The response content and A/B assignment are **blinded** to the human reviewer. "
        "Full results and private mapping must not be shown to the reviewer until scoring completes.",
        "- **No independent second human scores were obtained here.** No effectiveness or failure-rate "
        "improvement claim may be made until independent blind scoring; a fresh matched A/B is "
        "necessary but insufficient.",
        "- The original 152-request experiment and original human review are not rewritten.",
        "- Production default and history-pruning settings remain unchanged.",
        "",
        "## Reviewer files",
        "",
        "- [Blinded review packet](HUMAN_REVIEW.md)",
        "- [Blank scorecard](HUMAN_SCORECARD.csv)",
        "",
    ]
    write_new(out / "RESULTS.md", "\n".join(markdown))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--freeze", type=Path)
    actions.add_argument("--execute", type=Path)
    actions.add_argument("--report", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--private", type=Path)
    args = parser.parse_args()
    if args.freeze:
        print("FREEZE_RESULT", json.dumps(freeze(args.freeze)))
    elif args.execute:
        if not args.plan:
            parser.error("--execute needs --plan")
        execute(args.plan, args.execute)
    else:
        if not args.plan or not args.state or not args.private:
            parser.error("--report requires --plan, --state and --private")
        print("REPORT_RESULT", json.dumps(report(args.plan, args.state, args.report, args.private)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print("AGENCY_TRIAL_STOPPED", type(exc).__name__, str(exc)[:130], file=sys.stderr)
        raise SystemExit(1) from None
