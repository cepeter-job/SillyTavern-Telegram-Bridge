"""Freeze and run the new helper-correction study; never activate production pruning."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.issue421_helper_fixture import KINDS, MODEL, SCENARIOS, capture_case, digest  # noqa: E402
from tools.issue421_helper_wire import TrialWire, atomic_json  # noqa: E402

PROTOCOL = (
    "tools/issue421_helper_fixture.py",
    "tools/issue421_helper_wire.py",
    "tools/issue421_helper_study.py",
)


def protocol_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROTOCOL}


def freeze_plan(baseline: dict, candidate: dict, *, baseline_revision="", candidate_revision="") -> dict:
    """Inputs are explicit native captures from separate revision-pinned interpreters."""
    cases = []
    for scenario in SCENARIOS:
        for kind in KINDS:
            variants = {}
            for label, captures in (("baseline", baseline), ("candidate", candidate)):
                item = captures[scenario["id"]][kind]
                if item["status"] != "complete" or len(item["calls"]) != 1:
                    raise ValueError("matched_capture_incomplete")
                variants[label] = item["calls"][0]
            left, right = variants["baseline"], variants["candidate"]
            if (
                left["model"] != MODEL
                or right["model"] != MODEL
                or left["settings"]["max_tokens"] != right["settings"]["max_tokens"]
                or left["settings"]["temperature"] != right["settings"]["temperature"]
                or left["messages"][-1] != right["messages"][-1]
                or (kind == "story" and left != right)
            ):
                raise ValueError("matched_source_settings_or_control_mismatch")
            source_hash = baseline[scenario["id"]][kind]["source_sha256"]
            if source_hash != candidate[scenario["id"]][kind]["source_sha256"]:
                raise ValueError("matched_source_digest_mismatch")
            cases.append(
                {
                    "case_id": scenario["id"] + ":" + kind,
                    "scenario": scenario["id"],
                    "kind": kind,
                    "language": scenario["language"],
                    "source_sha256": source_hash,
                    "required_facts": scenario["required_facts"],
                    "forbidden_inferences": scenario["forbidden_inferences"],
                    "variants": variants,
                }
            )
    plan = {
        "protocol": "issue421-helper-correction-v1",
        "baseline_revision": baseline_revision,
        "candidate_revision": candidate_revision,
        "protocol_sha256": protocol_hashes(),
        "cases": cases,
        "model": MODEL,
        "maximum_requests": 24,
        "maximum_input_tokens": 300000,
        "maximum_output_tokens": 32000,
        "per_arm_maximum_requests": 12,
        "per_arm_maximum_input_tokens": 150000,
        "per_arm_maximum_output_tokens": 16000,
        "story_reduction_target": 0.20,
        "human_approved": False,
        "production_activation_allowed": False,
        "order": [{"variant": v, "case_id": c["case_id"]} for v in ("baseline", "candidate") for c in cases],
        "limitations": [
            "Two synthetic scenarios are not a representative production sample.",
            "Identical story requests are negative controls, not a story-pruning candidate.",
            "Baseline arm runs first; cache differences are confounded by execution order and route behavior.",
            "All repairs and continuations count within twelve physical requests per arm; no automatic resumption.",
        ],
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def verify_plan(plan: dict) -> None:
    copy = dict(plan)
    claimed = copy.pop("plan_sha256", None)
    if digest(copy) != claimed or plan.get("protocol_sha256") != protocol_hashes():
        raise ValueError("trial_plan_or_protocol_digest_mismatch")
    expected = {
        "maximum_requests": 24,
        "maximum_input_tokens": 300000,
        "maximum_output_tokens": 32000,
        "per_arm_maximum_requests": 12,
        "per_arm_maximum_input_tokens": 150000,
        "per_arm_maximum_output_tokens": 16000,
        "story_reduction_target": 0.20,
        "human_approved": False,
        "production_activation_allowed": False,
    }
    if any(plan.get(k) != v for k, v in expected.items()) or len(plan.get("cases", [])) != 10:
        raise ValueError("trial_plan_limits_invalid")


def run_arm(plan: dict, source_root: Path, output: Path, variant: str) -> None:
    verify_plan(plan)
    if output.exists() or output.is_symlink():
        raise ValueError("trial_output_exists")
    revision = subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=source_root,
        text=True,
        timeout=10,
    ).strip()
    if revision != plan[variant + "_revision"]:
        raise ValueError("trial_source_revision_mismatch")
    if subprocess.check_output(
        ["/usr/bin/git", "diff", "HEAD", "--", "bridge"],
        cwd=source_root,
        timeout=10,
    ):
        raise ValueError("trial_native_source_dirty")
    # No bridge module is imported before the selected arm's source takes precedence.
    sys.path.insert(0, str(source_root.resolve()))
    import os

    from bridge import provider_transport
    from bridge.environment import bootstrap_environment
    from bridge.model_router import ModelRouter
    from bridge.network_security import strict_urlopen
    from bridge.provider_catalog import load_routing_catalog
    from bridge.settings import load_app_settings
    from tools.native_context_limits import TrialBudget
    from tools.native_context_transport import configured_client

    env = dict(os.environ)
    bootstrap_environment(env)
    cfg = load_app_settings(env, home=Path.home())
    router = ModelRouter(lambda: load_routing_catalog(app_settings=cfg))
    admission = configured_client(MODEL, TrialBudget(maximum_requests=12))
    admission.quota(1)
    output.mkdir(parents=True, mode=0o700)
    wire = TrialWire(
        output / "attempts.json",
        quota=admission.quota,
        opener=strict_urlopen,
        maximum_requests=12,
        maximum_input=150000,
        maximum_output=16000,
    )
    scenarios = {s["id"]: s for s in SCENARIOS}
    for case in plan["cases"]:
        if wire.state["stopped"]:
            break
        wire.set_context(case["case_id"], variant)
        calls = []
        outputs = []

        def generate(request, *, calls=calls, outputs=outputs, case=case):
            if not calls and request != case["variants"][variant]:
                raise ValueError("trial_first_request_changed_after_freeze")
            calls.append(request)
            with patch.object(provider_transport, "strict_urlopen", wire):
                text = provider_transport.generate_provider_text(
                    router,
                    "",
                    request["model"],
                    request["messages"],
                    settings=request["settings"],
                    force_non_stream=True,
                    request_timeout=90,
                    app_settings=cfg,
                )
            outputs.append(text)
            return text

        try:
            result = capture_case(
                output / case["scenario"] / case["kind"], scenarios[case["scenario"]], case["kind"], generate=generate
            )
            status = result["status"]
        except Exception as error:
            status = "failed:" + type(error).__name__
        wire.state["logical_cases"].append(
            {"case_id": case["case_id"], "variant": variant, "status": status, "outputs": outputs}
        )
        atomic_json(wire.path, wire.state)
        print(
            json.dumps(
                {
                    "case_id": case["case_id"],
                    "variant": variant,
                    "status": status,
                    "physical_requests": len(wire.state["attempts"]),
                    "stopped": wire.state["stopped"],
                }
            ),
            flush=True,
        )
    atomic_json(
        output / "completion.json",
        {
            "plan_sha256": plan["plan_sha256"],
            "revision": revision,
            "logical_completed": len(wire.state["logical_cases"]),
            "physical_requests": len(wire.state["attempts"]),
            "stopped": wire.state["stopped"],
            "production_activation_allowed": False,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    freeze = modes.add_parser("freeze")
    for name in ("baseline", "candidate", "output"):
        freeze.add_argument("--" + name, type=Path, required=True)
    freeze.add_argument("--baseline-revision", required=True)
    freeze.add_argument("--candidate-revision", required=True)
    run = modes.add_parser("run-arm")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--source-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--variant", choices=("baseline", "candidate"), required=True)
    args = parser.parse_args()
    if args.mode == "freeze":
        if args.output.exists():
            parser.error("plan output already exists")
        plan = freeze_plan(
            json.loads(args.baseline.read_text()),
            json.loads(args.candidate.read_text()),
            baseline_revision=args.baseline_revision,
            candidate_revision=args.candidate_revision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.output, plan)
        print(json.dumps({"plan_sha256": plan["plan_sha256"], "planned_requests": len(plan["order"])}))
    else:
        run_arm(json.loads(args.plan.read_text()), args.source_root, args.output, args.variant)


if __name__ == "__main__":
    main()
