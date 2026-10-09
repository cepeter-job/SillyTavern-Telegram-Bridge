# Hybrid Shadow Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; implement in this session, checkpoint each verified deliverable.

**Goal:** Add source-verified hybrid shadow context without changing dispatched prompts.
**Architecture:** Native read-only source capture -> deterministic protected/relevant dialogue selection plus authorized Summary windows -> shadow-only metrics callback. Missing proof returns full baseline.
**Tech Stack:** Existing Python/SQLite/pytest; no dependencies or model calls.
**Spec:** docs/superpowers/specs/2026-10-09-hybrid-shadow-design.md

## Global Constraints
Preserve PR #467. No production activation, schema writes, provider calls or private transcript export. All non-history messages and recent protected tail remain exact. Receipt provenance is not semantic equivalence.

## Review Focus
- Rewritten sources/reader changes between capture and evaluation must invalidate the preview.
- Unauthorized archive blocks must never enter context or diagnostics.
- Anchors, short referents, repeated refusals and branch boundaries survive in original order.
- Large cards, mixed multimodal messages, long required continuity and multilingual ambiguity never induce forced pruning.
- Off/enabled runtime modes do not even execute the new shadow callback; shadow dispatch stays baseline.

## Tasks
- [x] Native source capture: add context_hybrid_sources.py and context_hybrid_types.py; real Summary worker fixture; RED/GREEN tests for coverage, interval gaps, archive corruption and scope isolation. Commit.
- [x] Hybrid selector: add context_hybrid_shadow.py and context_hybrid_policy.py; RED/GREEN tests for exact recent/nonhistory preservation, anchors/relevance, fallback and estimated savings without semantic claims. Commit.
- [x] Shadow wiring: append callable to MemoryPromptContext; prepare only in shadow mode, invoke metrics-only in context_selection_runtime. Test mode isolation and unchanged dispatch. Commit.
- [x] Offline evidence CLI: native synthetic unique-dialogue and negative controls, optional metadata-only read-only live audit; all provider request counts zero. Freeze results with code revision and limitations.
- [ ] Full local checks, fresh review, PR, issue #421 checkpoint and final report. Do not merge or deploy the unreviewed selector.

## Implementation rulings
- Archived windows retain their own accepted canonical-source/digest proof after the native checkpoint ring is pruned. Require the current Summary checkpoint; cross-check archived checkpoints while present but do not incorrectly require indefinite retention. A regression reproduced the native retention policy. Corrupt source, digest or audience still fails closed.

## Verification checkpoint
- Native-source, selector, shadow dispatch and offline CLI are implemented; 189 related tests and 65 subtests passed before the final implementation-hash regression was added.
- Host disk-journal stalls delayed the first broad test run; rerun used process-local tmpfs temporary test files, without changing project or live database settings.
- Initial native metadata audit retained all 73 turns of the larger session due to protected anchors/referents; the smaller session lacked complete Summary evidence. These zero-savings fallbacks are retained in reporting.
- Full branch review is local/self-review plus automated static and regression checks; no independent reviewer agent is available in this session. PR review remains required.
