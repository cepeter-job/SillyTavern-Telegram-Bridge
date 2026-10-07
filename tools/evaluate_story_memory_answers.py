#!/usr/bin/env python3
"""Plan synthetic generated-answer checks offline; model dispatch needs explicit caps."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from story_memory_answer_limits import AnswerLimits  # noqa: E402
from story_memory_answer_scoring import score_answer  # noqa: E402

SYSTEM = (
    "Evaluate only the supplied synthetic story memory. Return one JSON object with exactly "
    "answer (string), facts (list of {key,statement}), and narrative (string). "
    "Answer the question using only supplied facts, extracting their exact keys and canonical "
    "sentences into facts. Include each relevant canonical sentence verbatim in both answer "
    "and a short narrative continuation. You may add style, but no new world facts. "
    "If supplied memory cannot answer a question, explicitly say the answer is unknown and "
    "do not invent a fact. Do not use other stories or an omniscient audience."
)
LIMITATIONS = [
    "Curated synthetic fixture and author-predeclared judgments are not a general model benchmark.",
    "Structured extraction identity/statement checks and prose sentence mentions are separate.",
    "Prose checks cannot establish semantic truth, detect paraphrased leaks or contradictions, "
    "or distinguish identical cloned fact text; human review remains required.",
    "Narrative voice, coherence, agency and causal continuity are not automatically scored.",
    "UTF-8 wire bytes plus 256 framing tokens conservatively reserve ordinary byte-tokenized "
    "text. Nonstandard tokenizers, provider hidden prompts/reasoning and additional charges "
    "are outside this reservation; provider billing is not verified.",
    "max_tokens requests an output limit; provider enforcement and usage reporting are trusted "
    "only conditionally. Missing usage retains the full reserved cost.",
    "Production retrieval can omit a required fact; this controlled evaluation does not add "
    "oracle answers to the model context or change production memory.",
]


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--queries", default="Q01,Q14,Q19,Q23")
    parser.add_argument("--enable-live-model", action="store_true")
    for name in ("endpoint", "model", "key-env", "input-usd-per-million", "output-usd-per-million", "max-cost-usd"):
        parser.add_argument("--" + name)
    for name in (
        "request-budget",
        "input-token-budget",
        "output-token-budget",
        "max-output-tokens",
        "context-token-cap",
    ):
        parser.add_argument("--" + name, type=int)
    args = parser.parse_args(argv)
    queries = args.queries.split(",")
    if (
        not queries
        or len(queries) != len(set(queries))
        or any(value not in {f"Q{i:02}" for i in range(1, 25)} for value in queries)
    ):
        parser.error("queries must be unique predeclared Q01..Q24 identifiers")
    args.query_ids = queries
    live_names = (
        "endpoint",
        "model",
        "key_env",
        "input_usd_per_million",
        "output_usd_per_million",
        "max_cost_usd",
        "request_budget",
        "input_token_budget",
        "output_token_budget",
        "max_output_tokens",
        "context_token_cap",
    )
    if not args.enable_live_model:
        if any(getattr(args, name) is not None for name in live_names):
            parser.error("live configuration requires --enable-live-model")
        return args, parser, None, ""
    if any(getattr(args, name) is None for name in live_names):
        parser.error("live evaluation requires endpoint/model/key-env and every explicit limit and price")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.key_env) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", args.model
    ):
        parser.error("invalid explicit model or credential environment name")
    token = os.environ.get(args.key_env, "")
    if not token or len(token) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in token):
        parser.error("explicit credential environment variable is absent or invalid")
    try:
        limits = AnswerLimits(**{name: getattr(args, name) for name in AnswerLimits.__dataclass_fields__})
        if len(queries) > limits.request_budget:
            raise ValueError("planned_work_exceeds_explicit_budget")
    except ValueError as exc:
        parser.error(str(exc))
    return args, parser, limits, token


def build_plan(corpus, query_ids, *, model="offline-unexecuted", max_output_tokens=1000):
    from story_memory_retrieval_fixture import sha256
    from story_memory_retrieval_rank import fused_blocks

    facts = {key: stored.fact.summary for key, stored in corpus.facts.items()}
    key_by_id = {stored.memory_id: key for key, stored in corpus.facts.items()}
    cases = {case.query_id: case for case in corpus.cases}
    rows, payloads = [], []
    for query_id in query_ids:
        case, scope = cases[query_id], corpus.scopes[query_id]
        blocks, _ = fused_blocks(corpus.db, scope, case.text)
        selected = list(dict.fromkeys(key_by_id[item.memory_id] for block in blocks for item in block.evidence))
        context = "\n\n".join(block.text for block in blocks if block.text)
        user = {
            "question": case.text,
            "reader": case.reader,
            "session": case.session_key,
            "memory_context": context,
            "fact_bindings": [{"key": key, "statement": facts[key]} for key in selected],
        }
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, sort_keys=True)},
        ]
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "stream": False,
            "n": 1,
        }
        payloads.append(payload)
        rows.append(
            {
                "query_id": query_id,
                "scope": asdict(scope),
                "required_fact_keys": list(case.relevant_fact_keys),
                "forbidden_fact_keys": list(case.forbidden_fact_keys),
                "eligible_fact_keys": list(corpus.eligible(case)),
                "selected_fact_keys": selected,
                "required_missing_from_context": [key for key in case.relevant_fact_keys if key not in selected],
                "prompt_sha256": sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True)),
                "memory_context_sha256": sha256(context),
                "prompt": messages,
                "status": "not_executed",
            }
        )
    return rows, payloads, facts


def validate_decoded_strings(value, token):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if token in item:
                raise ValueError("credential_echo")
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("invalid_answer_unicode") from exc
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def decode_completion(data, *, token, limits, input_upper):
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
        raise ValueError("completion_schema")
    choice = data["choices"][0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise ValueError("completion_not_finished")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ValueError("completion_schema")
    # A deliberately stricter independent byte cap also bounds prose when usage is absent.
    try:
        content_bytes = len(content.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("invalid_answer_unicode") from exc
    if content_bytes > limits.max_output_tokens:
        raise ValueError("completion_text_byte_cap")
    if token in content:
        raise ValueError("credential_echo")
    usage = limits.validate_usage(data.get("usage"), input_upper)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(content, object_pairs_hook=unique_object)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid_answer_json") from exc
    validate_decoded_strings(value, token)
    return value, usage


def main(argv=None):
    args, parser, limits, token = parse_options(argv)
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_http import BoundedHTTP, StudyHTTPError
    from story_memory_retrieval_identity import source_identity

    client = None
    if limits:
        try:
            # Capture the bounded transport seam before the corpus installs offline guards.
            client = BoundedHTTP(
                args.endpoint,
                request_budget=limits.request_budget,
                phase_limits={"answers": len(args.query_ids)},
                deadline_seconds=300,
                token=token,
            )
        except ValueError:
            parser.error("invalid explicit model endpoint")
    identity = source_identity()
    source = {key: identity[key] for key in ("revision", "tree", "dirty", "source_sha256")}
    with tempfile.TemporaryDirectory(prefix="story-answer-evaluation-") as home:
        with RetrievalCorpus(Path(home)) as corpus:
            rows, payloads, facts = build_plan(
                corpus,
                args.query_ids,
                model=args.model or "offline-unexecuted",
                max_output_tokens=limits.max_output_tokens if limits else 1000,
            )
            report = {
                "schema_version": 1,
                "mode": "live_model" if limits else "offline_plan",
                "fixture_sha256": corpus.fixture_sha256,
                "source": source,
                "context_pipeline": "production_FTS_and_validated_memory_blocks",
                "judgment_origin": "predeclared_synthetic_fixture_author",
                "cases": rows,
                "model_evaluation": {"status": "not_executed"},
                "narrative_quality": {
                    "status": "human_review_required",
                    "rubric": [
                        "Voice and readability",
                        "Causal and temporal continuity",
                        "Character agency",
                        "Audience/branch knowledge and unsupported or contradictory claims",
                    ],
                },
                "limitations": LIMITATIONS,
                "network": {"requests": 0},
            }
            if limits:
                try:
                    reservation = limits.reserve(payloads)
                except ValueError as exc:
                    parser.error(str(exc))
                report["budget"] = {"limits": asdict(limits), "reservation": reservation}
                report["provider"] = {
                    "model": args.model,
                    "endpoint_sha256": client.identity["endpoint_sha256"],
                    "transport": client.identity["transport"],
                    "transport_version": client.identity["transport_version"],
                }
                report["model_evaluation"]["status"] = "completed"
                for index, (row, payload) in enumerate(zip(rows, payloads, strict=True)):
                    try:
                        result = client.request("POST", "", payload, phase="answers")
                        value, usage = decode_completion(
                            result.data,
                            token=token,
                            limits=limits,
                            input_upper=reservation["input_upper_bounds"][index],
                        )
                        scoring = score_answer(
                            value,
                            required=row["required_fact_keys"],
                            forbidden=row["forbidden_fact_keys"],
                            eligible=row["eligible_fact_keys"],
                            facts=facts,
                        )
                        row.update(
                            status="evaluated",
                            generated=value,
                            scoring=scoring,
                            provider_usage=usage,
                            elapsed_ns=result.elapsed_ns,
                        )
                    except (StudyHTTPError, ValueError) as exc:
                        # Only tool-defined categories; raw provider bodies and credentials never enter reports.
                        row.update(status="failed", error=exc.category if isinstance(exc, StudyHTTPError) else str(exc))
                        report["model_evaluation"]["status"] = "failed"
                        break
                report["network"] = {
                    "requests": client.count,
                    "observations": client.observations,
                    "automatic_retries": 0,
                }
                report["model_evaluation"]["evaluated_cases"] = sum(row["status"] == "evaluated" for row in rows)
                report["model_evaluation"]["machine_contracts_satisfied"] = all(
                    row.get("scoring", {}).get("machine_checks_satisfied", False) for row in rows
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    if not limits:
        return 0
    return 0 if report["model_evaluation"]["machine_contracts_satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
