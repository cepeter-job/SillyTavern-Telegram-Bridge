# Telegram Mini App Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task, with TDD and an isolated worktree.

**Goal:** Ship the private management Mini App and install.sh in six merged PR waves.
**Architecture:** Embedded loopback aiohttp transport over canonical services, authenticated private-chat scope, static ES modules, bounded background operations.
**Tech Stack:** Python 3.11+, aiohttp, SQLite, HTML/CSS/JavaScript, bash/systemd/Caddy.
**Spec:** docs/superpowers/specs/2026-09-27-miniapp-design.md

## Global constraints
Opt-in, loopback only, authenticated API, no CORS/secrets/client-selected chat, no Node build, no changes to ordinary chat, no edits to live .env during development. User delegates reviews/merges; no intermediate approval gates.

## Review focus
- Telegram menu launches may lack chat: derive private scope only from verified user ID.
- Concurrent bot generation: lock admission and stale session checks protect user mutations.
- Slow provider and disconnect: operation status survives transport cancellation; restart reports interruption.
- Uploaded PNG/JSON/document text: no traversal, credential reflection, or browser HTML injection.
- Install rerun with existing assets/config/services: preserve private state, validate before activation.

## Tasks
### Task 1: Authenticated shell
Create bridge/miniapp_auth.py, miniapp_config.py, miniapp_http.py, miniapp_runtime.py, miniapp_assets/{index.html,app.js,ui.js,style.css}; modify main.py and dependency manifests. API bootstrap returns only verified identity and feature availability. Runtime context owns bind/start/stop.
- [ ] Write tests/test_miniapp_auth.py and tests/test_miniapp_http.py (HMAC, age, duplicate query keys, unknown user, no auth, hostile origin, fixed assets, lifecycle).
- [ ] Run focused tests and observe missing-feature failures.
- [ ] Implement and run focused/full tests; lint/format/import policy/mypy; commit, PR, required CI, merge.
### Task 2: Character management and long operations
Create miniapp_context.py, miniapp_jobs.py, miniapp_characters.py and characters.js. Reuse prepare_character_optimization, proposals, native card ingestion/application and reference guards. Operations return {id,state,result,error} for their actor only.
- [ ] Write tests/test_miniapp_characters.py and tests/test_miniapp_jobs.py covering real temporary state and stale/foreign requests; run red.
- [ ] Implement catalog, portrait, upload, select/delete and optimizer preview/apply/discard, bounded operations and UI; focused/full checks; PR/merge.
### Task 3: Models and generation
Create miniapp_models.py and models.js; use ModelRouter, set_task_model, get/update_generation_settings and presets. Reject unknown/NaN/infinite/out-of-range values and never return provider specs.
- [ ] Write tests/test_miniapp_models.py; run red.
- [ ] Implement sanitized catalog and controlled session/preset writes plus UI; focused/full checks; PR/merge.
### Task 4: Sessions, personas and worlds
Create miniapp_sessions.py, miniapp_worlds.py and corresponding ES modules. Consume existing SessionService/PersonaService. Check ownership before load (load may create); protect active/default/referenced resources; require digest on world replacement.
- [ ] Write tests/test_miniapp_management.py; run red.
- [ ] Implement service-backed session/persona CRUD, bounded JSON world editor with backup/revision/usage checks and UI; focused/full checks; PR/merge.
### Task 5: Memory and Data Bank
Create miniapp_memory.py and memory.js; reuse memory/curator owners and RagService. Expensive indexing executes through bounded operations without DB transactions spanning network.
- [ ] Write tests/test_miniapp_memory.py; run red.
- [ ] Implement memory/curator/summary controls and RAG documents/search/upload/versions/activate/remove/reindex UI; focused/full checks; PR/merge.
### Task 6: Status, verified update and installation
Create miniapp_system.py, system.js, install.sh, tools/install_config.py and deployment documentation. Update uses the same verified engine, only configured service; no shell API. Installer builds a user venv and service, preserves env/assets, permits env-only setup, generates optional reverse proxy config and checks before activation.
- [ ] Write tests/test_miniapp_system.py and tests/test_installer.py; run red.
- [ ] Implement status/update/install, verify shell syntax and isolated repeat-install fixtures; run UI smoke/full suites/static checks; PR/merge; verify all PR states and final main. Do not claim live deployment.
