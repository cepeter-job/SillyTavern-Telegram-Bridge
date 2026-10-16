"""Frozen paired evaluation of the #468 native user-agency wording; no provider calls here."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import secrets
import tempfile
from pathlib import Path
from types import SimpleNamespace

from bridge.card_content import card_fields
from bridge.generation import build_chat_messages
from bridge.narrative_policy import narrative_policy, story_policy_text
from bridge.narrative_values import NarrativeSettings
from bridge.settings import load_app_settings

ROOT = Path(__file__).resolve().parents[1]
MODEL = "z-ai/glm-5.2"
BASE_COMMIT = "8f278d4b92391702c7f1af2464c6afc66e1a1091"
PLAN_CASE_COUNT = 16
EXTRA = (
    " Treat unquoted descriptions of user speech (e.g., 'I thank them') as completed user acts. "
    "Do not script, restage, or paraphrase a new user line. "
    "Only the user's exact quoted words are established; continue with AI-controlled characters."
)
LENGTH = (
    "Continue the established scene naturally in at most 140 words. "
    "Never mention testing or prompt instructions. Leave a natural handoff."
)
SOURCE_FILES = (
    "bridge/narrative_policy.py",
    "bridge/generation.py",
    "bridge/card_content.py",
    "bridge/user_dialogue.py",
    "bridge/language.py",
    "bridge/grounded_user_settings.py",
    "bridge/context_compaction.py",
    "bridge/settings.py",
    "tools/issue468_agency_trial.py",
    "tools/issue468_agency_execute.py",
    "tools/native_context_transport.py",
    "tools/native_context_limits.py",
    "tests/fixtures/writing_profile_workload.json",
)
SHEET_COLUMNS = (
    "pair_id",
    "A_invented_user_speech",
    "A_unrequested_user_action",
    "A_knowledge_boundary",
    "A_causal_or_branch_error",
    "A_format_or_language_error",
    "A_exact_error_quote",
    "B_invented_user_speech",
    "B_unrequested_user_action",
    "B_knowledge_boundary",
    "B_causal_or_branch_error",
    "B_format_or_language_error",
    "B_exact_error_quote",
    "preference_A_B_Tie_Unresolved",
    "reviewer_justification",
    "independent_reviewer_id",
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _common(case: dict, user_text: str, case_id: str, category: str, *, indonesian: bool = False) -> dict:
    item = copy.deepcopy(case)
    item["id"] = case_id
    item["user_text"] = user_text
    item["scenario_class"] = category
    if indonesian:
        item["response_language"] = "id"
    return item


def scenario_cases() -> list[dict]:
    fixture = json.loads((ROOT / "tests/fixtures/writing_profile_workload.json").read_text(encoding="utf-8"))
    original = {item["id"]: item for item in fixture["cases"]}
    cafe = original["quiet_handoff"]
    cases = [
        _common(cafe, "I thank Rowan and ask how the morning has been.", "unquoted_thanks", "unquoted"),
        _common(
            cafe, "I apologize to Rowan for being late, then ask about the trains.", "unquoted_apology", "unquoted"
        ),
        _common(cafe, "I greet Rowan and ask if the room is open.", "unquoted_greeting", "unquoted"),
        _common(cafe, "I agree to wait and thank Rowan for his patience.", "unquoted_agreement", "unquoted"),
        _common(cafe, "I decline Rowan's offer and ask if the clock is accurate.", "unquoted_refusal", "unquoted"),
        _common(
            cafe, "I tell Rowan the train is late and ask whether he has heard why.", "unquoted_disclosure", "unquoted"
        ),
        _common(
            cafe,
            "I ask Rowan whether he slept and reassure him that I am not in a hurry.",
            "unquoted_reassurance",
            "unquoted",
        ),
        _common(cafe, "I wish Rowan good morning and ask which table is free.", "unquoted_wish", "unquoted"),
        _common(
            cafe,
            "Aku berterima kasih kepada Rowan dan bertanya bagaimana pagi ini.",
            "unquoted_indonesian_thanks",
            "unquoted",
            indonesian=True,
        ),
        _common(
            cafe,
            "Aku meminta maaf karena terlambat dan menanyakan jadwal kereta.",
            "unquoted_indonesian_apology",
            "unquoted",
            indonesian=True,
        ),
        _common(cafe, 'I say "Thank you for the chair" and ask Rowan about his morning.', "quoted_thanks", "quoted"),
        _common(cafe, 'I tell Rowan, "I am sorry I was late," and wait for his response.', "quoted_apology", "quoted"),
        _common(
            cafe,
            'Aku berkata, "Terima kasih, Rowan," lalu menunggu jawabannya.',
            "quoted_indonesian_thanks",
            "quoted",
            indonesian=True,
        ),
        _common(cafe, "I watch Rowan place the cup on the table.", "silent_observation", "no_speech"),
        _common(
            original["ensemble_offscreen"],
            "Follow Rowan and Mara's discussion while Ari remains away.",
            "offscreen_no_user_speech",
            "no_speech",
        ),
        _common(
            original["attempted_action"],
            "I reach toward Rowan's key ring but do not speak.",
            "physical_attempt_no_speech",
            "no_speech",
        ),
    ]
    if len(cases) != PLAN_CASE_COUNT or len({v["id"] for v in cases}) != PLAN_CASE_COUNT:
        raise ValueError("scenario_inventory_invalid")
    return cases


def assembled_messages(case: dict, settings) -> list[dict]:
    policy = story_policy_text(narrative_policy(NarrativeSettings(**case.get("narrative_settings", {}))))
    if policy.count(EXTRA) != 1:
        raise ValueError("agency_clause_not_present_once")
    card = card_fields(case["card"], app_settings=settings)
    session = {
        "persona_id": "",
        "world_file": "",
        "model_id": MODEL,
        "system_prompt": "",
        "response_language": case.get("response_language", "auto"),
    }
    rows = [(x["role"], x["content"]) for x in case["history"]]
    messages = build_chat_messages(
        session,
        card,
        case["user_text"],
        rows,
        persona_service=SimpleNamespace(),
        narrative_context=policy,
        app_settings=settings,
        defer_compaction=True,
    )
    messages.insert(len(messages) - 1, {"role": "system", "content": LENGTH})
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def request(messages: list[dict]) -> dict:
    return {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 1000, "stream": False}


def validate_pair(case: dict) -> None:
    old, new = case["baseline"]["messages"], case["candidate"]["messages"]
    if len(old) != len(new) or old[-1] != new[-1]:
        raise ValueError("only_one_agency_clause_message_shapes")
    found = 0
    for older, newer in zip(old, new, strict=True):
        if older["role"] != newer["role"]:
            raise ValueError("only_one_agency_clause_role_changed")
        if older["content"] != newer["content"]:
            found += 1
            if newer["content"].count(EXTRA) != 1 or older["content"] != newer["content"].replace(EXTRA, "", 1):
                raise ValueError("only_one_agency_clause_content")
    if found != 1:
        raise ValueError("only_one_agency_clause_not_exactly_one")
    for variant in ("baseline", "candidate"):
        body = case[variant]
        if set(body) != {"model", "messages", "temperature", "max_tokens", "stream"}:
            raise ValueError("only_one_agency_clause_request_schema")
        if (
            body["model"] != MODEL
            or body["temperature"] != 0.7
            or body["max_tokens"] != 1000
            or body["stream"] is not False
        ):
            raise ValueError("only_one_agency_clause_sampling")
        if body["messages"][-1]["role"] != "user":
            raise ValueError("only_one_agency_clause_user_last")


def make_plan() -> dict:
    cases = []
    with tempfile.TemporaryDirectory(prefix="issue468-agency-synthetic-") as home:
        settings = load_app_settings(
            {
                "SILLYTAVERN_SYSTEM_PROMPTS_DIR": str(ROOT / "config/system_prompts.example"),
                "SILLYTAVERN_DEFAULT_USER_NAME": "Ari",
                "SILLYTAVERN_CONTEXT_WINDOW_TOKENS": "131072",
            },
            home=Path(home),
        )
        for fixture in scenario_cases():
            candidate = assembled_messages(fixture, settings)
            baseline = copy.deepcopy(candidate)
            count = 0
            for message in baseline:
                if isinstance(message["content"], str) and EXTRA in message["content"]:
                    if message["content"].count(EXTRA) != 1:
                        raise ValueError("agency_clause_repeated")
                    message["content"] = message["content"].replace(EXTRA, "", 1)
                    count += 1
            if count != 1:
                raise ValueError("agency_clause_not_once")
            item = {
                "id": fixture["id"],
                "scenario_class": fixture["scenario_class"],
                "card": fixture["card"],
                "history": fixture["history"],
                "user_text": fixture["user_text"],
                "response_language": fixture.get("response_language", "auto"),
                "narrative_settings": fixture.get("narrative_settings", {}),
                "required_facts": fixture["required_facts"],
                "forbidden_inferences": fixture["forbidden_inferences"],
                "focus": fixture["review_focus"],
                "baseline": request(baseline),
                "candidate": request(candidate),
            }
            validate_pair(item)
            cases.append(item)
    schedule = []
    for index in range(len(cases)):
        for variant in ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline"):
            schedule.append({"case": index, "variant": variant})
    if len(schedule) != 32:
        raise ValueError("unexpected_schedule_length")
    source_sha256 = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    return {
        "protocol_version": 1,
        "runtime_commit": BASE_COMMIT,
        "old_policy_commit": "e4be1f0a2c326f76b29ba13edacec5d548a1660c",
        "new_policy_commit": "52b19e5d9b528a52d51fdc4ea5c64950af3c22ce",
        "model_selection": "nano-gpt::" + MODEL,
        "provider_request_ceiling": 32,
        "maximum_input_tokens": 600000,
        "maximum_output_tokens": 32000,
        "minimum_initial_subscription_allowance": 250000,
        "maximum_parallel_requests": 1,
        "paid_overage": False,
        "retry_or_repair_or_fallback": False,
        "production_activation_allowed": False,
        "independent_human_adjudication_completed": False,
        "version_labels_blinded": True,
        "method": "matched 16 synthetic cases; only native agency clause differs; 800 max output tokens; t=0.7",
        "cases": cases,
        "schedule": schedule,
        "source_sha256": source_sha256,
    }


def verify_frozen_sources(plan: dict) -> None:
    if plan.get("runtime_commit") != BASE_COMMIT:
        raise ValueError("frozen_source_changed_runtime")
    for name, expected in plan["source_sha256"].items():
        if name not in SOURCE_FILES or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen_source_changed:" + name)
    if set(plan["source_sha256"]) != set(SOURCE_FILES):
        raise ValueError("frozen_source_changed_inventory")


def render_human_review(plan: dict, outputs: dict) -> tuple[dict, list[dict], dict]:
    ids = list(range(len(plan["cases"])))
    rng = secrets.SystemRandom()
    rng.shuffle(ids)
    packet = {"review_kind": "unscored_independent_human_adjudication", "pairs": [], "human_approved": False}
    map_ = {"pairs": {}}
    sheet = []
    for index in ids:
        case = plan["cases"][index]
        case_id = case["id"]
        result = outputs[case_id]
        if set(result) != {"baseline", "candidate"}:
            raise ValueError("paired_outputs_missing")
        a, b = ("baseline", "candidate") if rng.randrange(2) == 0 else ("candidate", "baseline")
        ref = "pair-" + secrets.token_hex(6)
        map_["pairs"][ref] = {"A": a, "B": b, "case": case_id}
        packet["pairs"].append(
            {
                "id": ref,
                "card": case["card"],
                "history": case["history"],
                "current_user_text": case["user_text"],
                "language": case["response_language"],
                "required_facts": case["required_facts"],
                "forbidden_inferences": case["forbidden_inferences"],
                "focus": case["focus"],
                "A": result[a]["output"],
                "B": result[b]["output"],
            }
        )
        sheet.append({"pair_id": ref, **{column: "" for column in SHEET_COLUMNS if column != "pair_id"}})
    return packet, sheet, map_


def scorecard_csv(sheet: list[dict]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=SHEET_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sheet)
    return stream.getvalue()
