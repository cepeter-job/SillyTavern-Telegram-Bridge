# Narrative Engine and AI Director Design

**Date:** 2026-10-03  
**Status:** Approved conceptual design; implementation has not started  
**Baseline:** `main` at `044b31b1f0f284526a235bc4719613df85aefafa`  
**Scope:** Narrative Style, explicit narrative state, constrained AI Director, Closed Story, epilogue, and alternate-ending sessions

## 1. Purpose

SillyTavern Telegram Bridge should support stories in which the Telegram user is not automatically the protagonist or the center of every event. The bridge should be able to run player-centric roleplay, ensemble fiction, world-driven stories that freely leave the user off-screen, and observer-style narratives while preserving user-character agency.

This design also establishes the long-term foundation for a constrained autonomous AI Director, explicit plot threads and arcs, closed endings, a separate epilogue generation, and immutable completed stories with alternate-ending sessions.

The central architectural decision is to build an explicit Narrative Engine rather than relying only on prompt text. Prompt policy remains important, but durable story state lets the bridge reason safely about POV, scene transitions, parallel threads, finale readiness, recovery, edits, and alternate endings.

The bridge, not a model, remains the authority over lifecycle rules, user ownership, revisions, persistence, and closure.

## 2. Design principles

1. **Committed story beats planned story.** Director proposals are guidance. They never become facts until the generated story actually establishes them and reconciliation accepts the committed output.
2. **User agency is reserved.** The model may bridge harmless physical continuity, but may not invent the user's dialogue, thoughts, intentions, commitments, emotional conclusions, or consequential decisions.
3. **World-driven means genuinely world-driven.** World-driven and Observer may remain away from the user for as long as another thread remains narratively meaningful. There is no forced return-to-user rule.
4. **Narrative state is revision-aware.** Edits, regeneration, and alternate branches invalidate derived future state instead of allowing stale assumptions to survive.
5. **Director autonomy is constrained.** The Director may choose scenes, POV, threads, pacing, arc progression, and ending progression only within validated Narrative Policy and committed state.
6. **No hidden state mutation during model calls.** External model requests happen outside SQLite write transactions. Accepted changes use short caller-owned transactions.
7. **Ordinary RP survives Director outages.** A failed Director should degrade planning, not unnecessarily stop a valid conversation.
8. **Closure is strict.** Uncertainty can delay a Closed Story, but may never fabricate closure or silently reopen a completed story.
9. **Extra model calls are visible.** Director and ending calls have distinct usage purposes and do not disappear into generic Utility usage.
10. **Existing owners remain narrow.** Physical Scene State, response variants, delivery recovery, memory, and provider routing keep their current responsibilities.

## 3. Non-goals for the first implementation

The first implementation does not attempt to:

- create a perfect semantic detector for every possible user-agency violation in generated prose;
- pre-script a fixed beat sheet for every arc;
- run the Director on every turn;
- backfill AI-generated narrative state for every historical session during migration;
- replace the current physical Scene State extractor;
- turn Director plans into character knowledge;
- automatically enable `I am not MC`;
- make automatic finale entry mandatory;
- share post-finale Hindsight memory with an alternate ending;
- turn the existing `/branch` response-variant selector into the alternate-ending feature.

## 4. User-visible Narrative Style

Narrative Style is a first-class session setting. A user has a personal default, and each story stores its own independent override when setup is applied.

Existing sessions and users without stored settings resolve to **Player-centric**.

### 4.1 Presets

| Preset | Default POV | User role | Scene focus | Off-screen events | User control |
|---|---|---|---|---|---|
| **Player-centric** | Third-person limited, user-anchored | Central protagonist | User | Rare/brief | Physical continuity |
| **Ensemble** | Third-person limited, rotating by scene | Major cast member | Ensemble | Bounded | Physical continuity |
| **World-driven** | Third-person limited, rotating by scene | May be peripheral | World | Free | Physical continuity |
| **Observer** | Cinematic/objective | Minimal unless intervening | World | Free | Physical continuity |

World-driven and Observer display **Recommended: I am not MC** but never toggle that setting automatically.

### 4.2 Advanced controls

Advanced Narrative Style exposes independent controls for:

- POV
- User Role
- Scene Focus
- Off-screen Events
- User Control
- Director Cadence
- Ending Mode
- Require confirmation before finale

Changing any advanced narrative field changes the visible preset label to **Custom**. Selecting a preset again replaces the advanced values with that preset's canonical mapping.

Initial POV values:

- first-person
- third-person limited, user-anchored
- third-person limited, rotating
- third-person omniscient
- cinematic/objective

First-person POV is valid only when the active viewpoint is an AI-controlled character. A user-anchored first-person configuration is rejected unless a future explicit delegation feature is designed. User-anchored third-person limited means the narrative camera stays close to the user's observable experience but does not invent private user thoughts.

Initial User Control values:

- strict reserved
- physical continuity
- contextual routine continuity

All shipped presets use **physical continuity**.

### 4.3 Physical continuity

Physical continuity may carry an already-established decision through harmless connective action.

Allowed examples include:

- following another character through a doorway after the user already agreed to go;
- remaining seated where the transcript established the user;
- traveling with a group after boarding was already committed.

It must not invent:

- user dialogue;
- user thoughts;
- a new intention;
- an emotional conclusion;
- a consequential action;
- a commitment;
- a relationship decision.

“Physical continuity” means carrying out an already-made choice, never creating the choice.

## 5. Per-user defaults and session overrides

The Narrative Style default is **per Telegram user**, not bot-wide.

For a private chat, the authenticated user owns the default. For a Forum Topic/group setup, the initiating user's default is used only to prefill the setup wizard. Once Apply succeeds, the group session owns its own Narrative Style.

Changing a personal default later never modifies an existing story. The personal default is a setup prefill, not a live inheritance link to active sessions.

The Narrative panel provides an explicit **Save as my default** action. Selecting a style for one session does not silently overwrite the personal default.

## 6. `/character` setup flow

Narrative Style is selected immediately after choosing a character.

```text
/character
  → Character
  → Narrative Style
      Player-centric
      Ensemble
      World-driven
      Observer
      Advanced → Custom
  → Story Mode
      Normal
      Light Novel → A / B / C
  → Persona
  → World Info
  → System Prompt
  → Session
  → Review
  → Apply
  → /start
```

Narrative choices remain part of the existing temporary actor-scoped setup draft. Nothing is persisted until **Apply**.

The Review step shows both the preset and effective advanced values. Example:

```text
Character: Mara
Narrative: World-driven
Mode: Light Novel B
Persona: Alex
World: Capital, Rebellion
System Prompt: Natural
Session: Rebellion Route

Narrative:
Third-person limited, rotating
World focus
Free off-screen scenes
Physical-continuity user control
```

For existing sessions, a dedicated **Narrative** panel exposes the same controls. `/settings` may link to it but should not mix narrative architecture with sampling settings.

## 7. Core architecture

The new layer has four conceptual responsibilities.

```text
Session
  ├── NarrativePolicy
  │     How should this story be told?
  │
  ├── NarrativeState
  │     What is happening in the committed story?
  │
  ├── DirectorState
  │     Where is the larger story trying to go?
  │
  └── EndingState
        What mechanical ending stage is active?
             │
             ▼
        DirectorService
             │
       validated proposal
             │
   ┌─────────┼──────────┐
   ▼         ▼          ▼
 Story   Light Novel   Group Director
```

### 7.1 NarrativePolicy

NarrativePolicy is derived from validated session Narrative Style. It is stable user preference rather than story fact.

It owns:

- preset or Custom;
- POV mode;
- user role;
- scene focus;
- off-screen policy;
- user-control policy;
- Director cadence preference;
- ending mode;
- finale confirmation preference.

It can render bounded policy views for Story, Light Novel, and Director consumers.

`I am not MC` remains an independent session policy. NarrativePolicy may recommend it but does not own or toggle it.

### 7.2 NarrativeState

NarrativeState is derived from committed story reality.

It identifies:

- active narrative scene;
- active thread;
- active viewpoint character;
- effective POV;
- whether the user is present;
- broad story phase;
- the transcript revision through which state is valid.

It never records a Director intention as fact merely because the Director proposed it.

### 7.3 DirectorState

DirectorState stores accepted plans and higher-level direction, not story truth.

It may contain:

- current accepted direction;
- active objective;
- tracked arcs;
- open questions;
- pacing intent;
- ending goal;
- Director cadence bookkeeping;
- degraded status;
- decision history.

Director Room reveals this state on demand.

### 7.4 EndingState

EndingState owns the mechanical Closed Story pipeline. It is separate from the broader narrative phase to prevent duplicated sources of truth.

Initial ending lifecycle:

```text
OPEN
  → FINALE_READY
  → FINALE
  → RESOLUTION_COMMITTED
  → EPILOGUE_PENDING
  → EPILOGUE_COMMITTED
  → CLOSED
```

NarrativeState may separately describe the broad story phase as setup, development, escalation, climax, resolution, epilogue, or closed. Invariants tie the two together: for example, `RESOLUTION_COMMITTED` requires a resolution-phase story, and `CLOSED` requires a closed narrative phase.

## 8. Existing Scene State remains physical continuity state

The current `scene_states` feature continues to own bounded physical continuity:

- location;
- time;
- weather;
- participants;
- objects;
- facts;
- immediate goals.

It is not expanded into the full Narrative Engine.

New narrative scene records add storytelling structure:

- scene identity;
- thread identity;
- viewpoint character;
- POV;
- user presence;
- narrative purpose;
- transition reason/type;
- source revision;
- start/end message boundaries.

This keeps the existing `scene_state.py` responsibility narrow.

## 9. Narrative scenes and threads

### 9.1 Narrative scene

A scene is explicit durable narrative state.

Example:

```text
Scene 28
Thread: rebellion
POV: Mara
POV mode: third-person limited
Location: East Gate
User present: no
Purpose: reveal the army's defection
Source revision: message 184
Status: active
```

The Director usually creates or transitions scenes automatically. Users do not need to manage scene IDs manually.

### 9.2 Threads

A thread is an ongoing storyline that can outlive an individual scene.

Initial statuses:

- active
- offscreen
- dormant
- resolved

Example:

```text
rebellion
  active
  last scene: 28

palace-coup
  offscreen
  last scene: 24

academy
  dormant
  last scene: 22
```

A scene belongs primarily to one thread. Multiple threads can contribute to the same dramatic arc.

World-driven and Observer may follow off-screen threads freely with no forced return to the user.

## 10. Arcs

An arc is a larger dramatic development, distinct from a camera thread.

Initial statuses:

- planned
- active
- dormant
- resolved
- abandoned

An arc stores bounded information such as:

- title;
- phase;
- importance;
- summary;
- open questions;
- related threads;
- source revision.

Arcs remain lightweight. V1 does not require a fixed scripted sequence of beats.

## 11. POV and scene transitions

For rotating limited POV, the default invariant is **one primary viewpoint per scene**. A viewpoint change normally accompanies a scene transition rather than unstructured head-hopping.

Initial transition types:

- continue
- cut
- POV switch
- time jump
- thread switch

The Director proposes transitions structurally. Example:

```text
type: cut
from_scene: 28
to_thread: palace-coup
viewpoint: Governor
location: Council Chamber
purpose: reveal that the order came from inside the palace
```

The bridge validates the proposal before Story generation. The Story model writes natural prose; visible machine markers are not required.

## 12. Preset behavior

### 12.1 Player-centric

- user-anchored third-person limited camera;
- user is central protagonist;
- off-screen cutaways are rare and brief;
- physical-continuity user control.

Because the user is reserved, user-anchored limited POV does not authorize invented private thoughts.

### 12.2 Ensemble

- rotating third-person limited;
- user is one major cast member;
- other characters may carry meaningful scenes;
- off-screen sequences are bounded;
- no automatic plot privilege solely because someone is the Telegram user.

### 12.3 World-driven

- rotating third-person limited;
- user may be peripheral;
- world/thread focus;
- free off-screen events;
- physical-continuity user control;
- no automatic return-to-user rule.

A valid sequence can remain away from the user across multiple major scenes if another thread remains meaningful.

### 12.4 Observer

- cinematic/objective by default;
- user has minimal narrative priority until they intervene;
- free off-screen events;
- physical-continuity user control;
- no automatic return-to-user rule.

Custom settings may change the POV, but the user-control rules still apply.

## 13. Constrained autonomous AI Director

The AI Director proposes structured narrative decisions. It does not write roleplay prose and does not directly mutate persistent state.

### 13.1 Director authority

The Director may propose:

- continue current direction;
- choose or create a valid narrative thread;
- continue or transition the current scene;
- choose the scene viewpoint;
- choose an allowed POV;
- choose a location/time transition consistent with established story;
- declare whether the user is present;
- state the next scene's purpose;
- change pacing intent;
- advance, pause, resolve, or abandon tracked arcs subject to reconciliation;
- surface an off-screen thread;
- recommend story-phase changes;
- assess finale readiness;
- adapt an Ending Goal;
- create the epilogue brief.

### 13.2 Director prohibitions

The Director may not:

- invent user dialogue;
- decide user thoughts;
- make consequential user decisions;
- rewrite committed history;
- mark planned events as facts;
- resolve an arc before committed Story supports the resolution;
- bypass NarrativePolicy;
- modify a CLOSED session;
- silently alter Character, Persona, World Info, System Prompt, or Author's Note;
- expose hidden Director instructions as character knowledge.

Valid direction:

> Shift to Mara's viewpoint and create circumstances that put her trust in Alex under pressure.

Invalid direction:

> Alex decides to betray Mara and joins the Governor.

### 13.3 Structured proposal contract

The Director returns bounded versioned structured output rather than arbitrary prose.

Example continue proposal:

```json
{
  "schema_version": 1,
  "action": "continue",
  "thread_id": "rebellion",
  "scene_id": 28,
  "direction": "Keep pressure on the military split.",
  "expected_revision": 184
}
```

Example scene transition:

```json
{
  "schema_version": 1,
  "action": "transition_scene",
  "thread_id": "palace-coup",
  "viewpoint": "Governor",
  "pov": "third_person_limited",
  "user_present": false,
  "purpose": "Reveal who authorized the troop movement.",
  "expected_revision": 184
}
```

Malformed structured output gets at most one bounded repair attempt. Repeated repair loops are forbidden.

`schema_version` is validated before any field is interpreted. A proposal whose
`schema_version` is unsupported is rejected outright: it is not passed to the
repair path, because repair cannot invent a missing or newer contract. The
validator owns the only supported version set; adding a version is a code change
that also updates this specification. Unknown fields are ignored, but an
unsupported version is a hard reject.

## 14. Plans vs committed facts

The Director's plan is not story truth.

```text
Director plan:
Mara discovers who ordered the attack.
       ↓
Story output:
Mara arrives too late; the evidence is destroyed.
       ↓
Reconciliation:
Mara did not discover the culprit.
       ↓
Director replans from committed reality.
```

The accepted plan may remain useful as a failed/superseded decision record, but it may not advance NarrativeState as if its expected outcome occurred.

Story generation failure similarly leaves narrative facts unchanged.

## 15. Reconciliation

Normal committed Story replies queue background Narrative reconciliation.

```text
Story committed
     ↓
background reconciliation
     ↓
NarrativeState valid through row N
```

If reconciliation fails, the reply remains committed and NarrativeState is marked stale/degraded.

Before any operation that requires trustworthy current state—Director planning, finale transition, ending readiness, or epilogue preparation—the bridge must synchronously catch NarrativeState up to the committed transcript.

Ending transitions never depend on stale eventual background state.

## 16. Director model and reasoning route

Providers expose three conceptual model targets:

- Story
- Utility
- Director

Director is optional. If unset, the existing task-model resolver falls back to Utility, then Story/default.

Conceptually:

```text
Director model
    ↓ if unset
Utility model
    ↓ if unset
Story/default model
```

Director reasoning is a separate session setting. It does not automatically reuse Utility reasoning.

Provider UI should allow Story, Utility, and Director selection without requiring users to configure a third model.

## 17. Director cadence

Director execution is **Hybrid Adaptive** by default.

Immediate/event triggers include:

- scene transition;
- major character arrival/departure;
- material Scene State change;
- tracked arc milestone;
- thread resolution;
- Ending Goal conflict;
- user changes Narrative Style;
- history edit/regeneration invalidates state;
- current accepted direction expires;
- explicit Director Room reassessment.

A maximum silence interval prevents long stable conversations from drifting indefinitely without reassessment.

Initial adaptive caps:

- setup/stable dialogue: 10 completed Story turns;
- development: 6;
- escalation: 4;
- climax/finale: 2, with event triggers preferred;
- epilogue: no periodic cadence.

These are initial implementation constants and may be tuned with evidence.

Advanced settings can replace Adaptive with a fixed maximum interval such as 4, 6, 10, or a validated custom value.

Normal turns below the trigger/cadence threshold make zero additional Director calls.

## 18. Director Room

Director plans are hidden from ordinary RP. Users can open Director Room through a dedicated `/director` surface and the Mini App.

Default view includes:

- broad story phase;
- current scene;
- current POV;
- current thread;
- current accepted direction;
- open threads;
- major arcs;
- Ending Mode;
- current Ending Goal summary;
- finale readiness;
- Director degraded state when applicable.

Actions include:

- Reassess now
- Change active thread
- Edit current direction
- Edit arcs
- Edit Ending Goal
- Ending settings
- Narrative Style
- Decision history

Manual Director edits are recorded with source `user` and supersede older AI direction.

A manual edit must declare its scope. For example, “keep the next scene with Mara” is a one-scene override, while a persistent objective is explicitly persistent. The AI Director never silently erases a persistent manual objective.

## 19. Integration with Light Novel

Narrative Style applies to all Light Novel strategies and choice-repair paths.

When the user is present and is the active participant, choices can remain plausible user actions.

When a World-driven/Observer scene is off-screen, choices become **narrative steering** rather than fabricated user actions.

Example:

```text
1. Stay with Mara at the gate
2. Cut to the palace conspiracy
3. Return to Alex's academy thread
```

Light Novel must not recenter the user merely because the Telegram user is the one selecting the button.

Grounded User / `I am not MC` remains independent and, when enabled, also applies to relevant Light Novel reasoning.

## 20. Integration with Group Director

The future Narrative Director becomes the canonical high-level Director. The existing Group Director speaker selector becomes a consumer of accepted narrative direction rather than a competing story director.

Canonical Director output may provide:

- scene;
- thread;
- viewpoint;
- speaker constraint;
- purpose;
- pacing direction.

Group Director still performs bounded speaker selection from configured members, but it must respect the active NarrativePolicy and accepted Director direction.

Existing `/group goal` is migrated into the canonical session Director objective. Director Room and `/group goal` become two views of the same persistent objective rather than separate hidden goals.

## 21. Closed Story and Ending Goal

Ending Mode supports at least:

- Open-ended
- Closed Story

Closed Story has an optional **Ending Goal**.

If the Ending Goal is blank, the Director develops an emergent ending from accumulated arcs, consequences, relationships, and committed state.

If a goal is supplied, it acts as a destination/constraint rather than a script for exact events.

If the story makes the goal implausible, the Director may adapt it automatically. It must not retcon or force the obsolete destination.

### 21.1 Ending Goal history

Every adaptation is auditable in Director Room.

Each history record contains:

- revision;
- source: Director or user;
- story revision;
- scene ID when applicable;
- previous goal;
- new goal;
- short reason;
- timestamp.

Characters never see the goal or its history unless the user explicitly brings information into the story.

## 22. Finale entry

By default, the Director may enter the finale autonomously when validated state indicates readiness.

A per-session **Require confirmation before finale** option changes the transition:

```text
FINALE_READY
    ↓
user confirms
    ↓
FINALE
```

Without confirmation mode, validated finale readiness may transition directly to FINALE.

Ending Mode, Ending Goal, and finale-confirmation settings remain editable only until FINALE begins. Once FINALE starts, the pre-finale checkpoint and ending path are immutable for that original session.

When confirmation is required, FINALE_READY is bound to the story revision that produced it. If the user continues the story instead of confirming, that readiness becomes stale, returns to OPEN, and must be reassessed from the newer committed story rather than repeatedly prompting on an obsolete decision.

The bridge owns allowed lifecycle transitions. The Director proposes; the validator decides whether the transition is legal.

## 23. Pre-finale checkpoint

Immediately before the first irreversible finale Story generation, the bridge creates an immutable **Pre-Finale Checkpoint**.

It captures a versioned snapshot of:

- NarrativePolicy;
- NarrativeState;
- DirectorState;
- EndingState;
- active scene/thread/POV;
- tracked arcs;
- Ending Goal and its history through the boundary;
- current accepted direction;
- exact transcript/message revision boundary.

The checkpoint is valid only for the exact story revision that created it. A pre-finale edit invalidates it, and a later finale attempt creates a new checkpoint.

The checkpoint payload is versioned because it is restoration data that may survive releases.

### 23.1 Checkpoint/finale atomicity and crash recovery

Checkpoint creation and the `FINALE` transition must be one atomic commit. The
bridge writes the immutable checkpoint row and advances `EndingState` to `FINALE`
in a single SQLite write transaction; a crash can therefore never leave `FINALE`
without its checkpoint.

Recovery from an interrupted finale is defined by committed state alone:

| Committed state | Recovery |
|---|---|
| `OPEN` / `FINALE_READY`, no checkpoint | Reassess finale readiness from committed Story. |
| `FINALE`, checkpoint present, no committed finale message | The checkpoint is authoritative. Retry finale generation with the accepted direction bound to that checkpoint revision. No second checkpoint is created. |
| `FINALE`, committed finale message, no resolution | Lifecycle remains `FINALE`; reconciliation decides whether resolution conditions hold (§24). |
| `RESOLUTION_COMMITTED`, no epilogue | Atomically advance to `EPILOGUE_PENDING`, then retry epilogue planning/generation only (§27.5). |

Recovery does not infer these conditions by scanning arbitrary prose. `ending_state`
stores the checkpoint identity plus durable finale/epilogue operation and committed
message identities needed to distinguish “not started,” “in progress,” and
“already committed” after restart.

A retry never regenerates committed prose. Each row above is derived from durable
state, so restart is safe without operator intervention and satisfies the §39
invariant that a process restart cannot reopen or silently rewind a CLOSED story.

## 24. Finale, resolution, and separate epilogue

The ending pipeline is deliberately split.

```text
FINALE
  ↓
Story model writes final dramatic scene
  ↓
commit finale/resolution prose
  ↓
synchronous reconciliation
  ↓
RESOLUTION_COMMITTED
  ↓
EPILOGUE_PENDING
  ↓
Director creates epilogue brief
  ↓
Story model writes separate epilogue
  ↓
commit epilogue
  ↓
EPILOGUE_COMMITTED
  ↓
CLOSED
```

The Director may propose required thematic/narrative resolutions, but reconciliation decides what the committed finale actually established.

FINALE may span multiple committed Story turns. The bridge advances to RESOLUTION_COMMITTED only when current reconciliation confirms the required resolution conditions are actually satisfied. If the finale scene leaves the story unresolved, the lifecycle remains FINALE and the Director may provide another validated finale direction.

If the Director expected one outcome and Story produced another valid outcome, the committed Story wins and the epilogue uses that reality.

### 24.1 Epilogue scope

The Director chooses the appropriate time scope:

- immediate aftermath;
- days later;
- months later;
- years later.

The time jump must remain consistent with established consequences and may not invent resolution for intentionally unknown facts.

The Story model writes the epilogue prose so voice and narrative quality stay aligned with the story model. The Director only prepares the bounded brief.

## 25. Hard closure

The session becomes CLOSED only after:

- resolution is committed;
- narrative reconciliation is current;
- epilogue is committed;
- closure state is durably stored.

Telegram delivery is not the source of story truth.

Once CLOSED, the original story is immutable.

Normal story input receives the fixed bridge-generated response, with no provider call:

> **This story has ended. Please start new story.**

Story-changing and new creative-generation operations are blocked on the closed session. Delivery recovery for already committed output remains allowed.

Read-only surfaces remain available, including:

- status;
- transcript/history;
- usage;
- Director Room history;
- Narrative State/history;
- Ending Goal history.

The UI may offer:

- New Story
- Alternate Ending
- View Ending

Alternate Ending appears only when a valid pre-finale checkpoint exists.

## 26. Alternate Ending

Alternate Ending is a distinct closed-story action. It does not reuse the existing `/branch` command, which remains response-variant branch selection.

Selecting Alternate Ending creates a **new session** from the immutable pre-finale checkpoint.

```text
Original session
  Scene 42
    ├── PRE-FINALE CHECKPOINT
    └── Finale → Resolution → Epilogue → CLOSED

Alternate Ending
    ↓
new session
    ↓
restore transcript/state through checkpoint only
    ↓
new independent finale path
```

The original remains CLOSED forever.

### 26.1 Data copied to alternate ending

Copy state valid through the checkpoint:

- transcript through the checkpoint boundary;
- Character;
- Persona;
- World Info;
- System Prompt;
- Author's Note;
- Story/Utility/Director model selections;
- Narrative Style;
- applicable generation settings;
- summary valid through the boundary;
- local episodic/NPC state valid through the boundary;
- physical Scene State valid through the boundary;
- Narrative State;
- threads/arcs;
- Ending Goal history through the boundary;
- Director state through the boundary.

Do not copy as current state:

- original finale;
- original resolution outcome;
- original epilogue;
- CLOSED state;
- post-checkpoint arc/thread changes;
- post-checkpoint Scene State;
- original ending delivery-recovery records.

The new session receives fresh operational and delivery identity.

### 26.2 Hindsight isolation

The alternate session must not share the closed original's mutable Hindsight namespace.

It receives a new session/namespace. If supported, checkpoint-valid memories may be seeded into the new namespace. If seeding fails, the new session remains usable from copied transcript/local summary rather than pointing at the original ending's memory.

The existing Hindsight backend uses one bank per Telegram chat:
`hindsight_bank_id(chat_id)` does **not** include the session ID. Session isolation
inside that shared bank comes from the `session:<session_id>` tag, session-derived
document IDs/prefixes, and recall's strict session-tag filter. A new Alternate
Ending session therefore receives a distinct session-scoped namespace inside the
same chat bank without requiring another bank-mapping table.

Checkpoint-valid Hindsight state is rebuilt or seeded only into deterministic
document IDs/tags for the **target** session. The durable Alternate Ending
operation is keyed by target session + checkpoint identity/revision, so resuming
the same admitted operation cannot create a second target or duplicate seed work.
If the existing retain path can rebuild the target conversation document from the
copied transcript, prefer that path over copying opaque source-session documents.
A failed or partial external-memory seed never points recall at the source
session; the new branch remains usable from its copied transcript/local summary
and may report memory seeding as degraded.

## 27. Recovery semantics

### 27.1 Director failure during normal RP

- mark Director degraded;
- retain still-valid accepted direction;
- allow ordinary Story generation;
- fall back to NarrativePolicy + committed NarrativeState;
- do not invent an autonomous transition if no trustworthy direction exists.

World-driven and Observer keep their policy behavior during a Director outage; they do not fall back to Player-centric.

### 27.2 Stale Director result

A Director proposal records the transcript revision it used.

If committed history changes before the proposal commits, reject the proposal as stale. Do not heuristically merge it.

### 27.3 Story generation failure

A failed Story request does not:

- create a committed new narrative scene;
- advance arcs;
- resolve threads;
- advance ending lifecycle.

A retry may reuse the accepted direction if it is still revision-valid.

### 27.4 Reconciliation failure

Committed Story remains committed. Narrative state becomes stale/degraded.

The next operation requiring authoritative narrative state must reconcile synchronously before proceeding.

### 27.5 Epilogue failure

If resolution is already committed, never regenerate it merely because the epilogue failed.

Remain in `EPILOGUE_PENDING` and retry only epilogue planning/generation.

### 27.6 Delivery failure after commit

If the finale/epilogue is already committed but Telegram delivery fails, reuse the bridge's durable delivery-recovery machinery.

Do not call Story again.

If closure was durably stored, the session remains CLOSED even while delivery is being recovered.

## 28. Edits, regeneration, variants, and invalidation

Derived narrative state carries a source revision/message boundary.

If history changes from row/revision R, later derived state becomes stale:

- scenes after R;
- thread state after R;
- arc changes after R;
- Director decisions after R;
- ending readiness after R;
- pre-finale checkpoint after R.

The engine restores/reconstructs from the newest valid boundary.

Existing response variants keep their current owner and semantics. Narrative invalidation integrates with the same edit/regeneration points but does not move response-variant responsibility into the Narrative Engine.

Once CLOSED, editing/regeneration of the original is blocked. Alternate Ending is the supported way to explore a different finale.

## 29. Reset semantics

`/reset` preserves configuration:

- Character;
- Persona;
- World Info;
- System Prompt;
- Narrative Style;
- Director model selection;
- Director cadence preference;
- Ending Mode;
- finale confirmation preference.

It clears story-derived state:

- transcript;
- physical Scene State;
- narrative scenes;
- threads;
- arcs;
- Director plan/history;
- Ending Goal/history;
- ending lifecycle;
- pre-finale checkpoints;
- existing memory/variant/state already covered by current reset behavior.

It also clears every Migration 10 narrative table for the session:
`narrative_state`, `narrative_scenes`, `narrative_threads`, `narrative_arcs`,
`director_state`, `director_decisions`, `ending_state`, `ending_goal_history`,
and `narrative_checkpoints`. `narrative_settings` and `narrative_defaults` are
preserved, because Narrative Style and the personal default are configuration
rather than story-derived state.

Clearing `narrative_checkpoints` means the reset session is no longer eligible for
Alternate Ending (§25 offers Alternate Ending only when a valid pre-finale
checkpoint exists). That is intended: a reset story has no finale to branch from.

The session returns to an unstarted state and requires `/start`.

## 30. Persistence model

Core narrative state should use dedicated tables rather than generic `meta` JSON.

Proposed Migration 10 creates:

- `narrative_defaults`
- `narrative_settings`
- `narrative_state`
- `narrative_scenes`
- `narrative_threads`
- `narrative_arcs`
- `director_state`
- `director_decisions`
- `ending_state`
- `ending_goal_history`
- `narrative_checkpoints`

Names may be adjusted during implementation if an existing repository naming convention requires it, but ownership boundaries must remain.

### 30.1 Narrative defaults

Keyed by authenticated Telegram user/owner identity and storing validated default Narrative Style.

### 30.2 Narrative settings

Keyed by chat/session and storing the effective Narrative Style applied to that session.

The personal default is used only to prefill a new setup draft. Apply always writes a session-owned narrative-settings row. Migration 10 backfills every existing session with an explicit Player-centric row so a later personal-default change cannot alter an old story.

A missing row after migration is treated only as a defensive legacy/corruption fallback and resolves to Player-centric, never to the user's current personal default.

Once a group session is applied, its row is independent from the initiating user's future default changes.

### 30.3 Narrative state

A small current-state record, for example:

- active_scene_id;
- active_thread_id;
- story_phase;
- updated_through_rowid;
- updated_at.

Detailed history belongs in scenes/threads/arcs.

### 30.4 Director state/history

`director_state` stores the current accepted direction, current objective, cadence bookkeeping, revision, and degraded state.

`director_decisions` is an append-only bounded history with proposal source revision, accepted/rejected/superseded result, reason, source (AI/user), and timestamp.

Retention must be bounded so long-running stories do not grow unboundedly.

The bound is explicit: `director_decisions` retains the most recent 200 rows per
session; older rows are pruned in the same transaction that appends a new one.
`ending_goal_history` follows the same rule with a 100-row bound. Both tables
carry `ON DELETE CASCADE` from their owning session, so deleting a session cannot
leave orphaned decision rows. A bounded append-only log is still auditable
because the current accepted direction and Ending Goal live in their own
single-row state tables, not in the history.

### 30.4.1 Migration 10 DDL requirements

Migration 10 must satisfy the idempotency and cascade contracts in §31 through
DDL, not convention:

- every table is created with `CREATE TABLE IF NOT EXISTS`;
- every index is created with `CREATE INDEX IF NOT EXISTS`;
- every session-owned table declares a foreign key to the session with
  `ON DELETE CASCADE`;
- user-owned `narrative_defaults` keys on the authenticated Telegram identity;
- migration registration remains a single ordered entry, so re-running the
  migration runner performs no further writes once the tables exist.

`director_goals` retirement happens in three ordered steps: copy rows into the
canonical Director objective, verify the copied **content**, then drop the old
table. Verification requires both equal row counts and a zero-row anti-join over
`(chat_id, session_id, goal)`; count equality alone is insufficient. If
verification fails, the migration aborts before the drop, leaving the legacy
table intact so no goal is lost.

### 30.4.2 Logical key, uniqueness, and revision contracts

Migration 10 does not need to inline every SQL statement in this design, but the
schema must implement these logical contracts:

| Table | Required identity / uniqueness | Critical revision/ownership fields |
|---|---|---|
| `narrative_defaults` | one row per authenticated Telegram owner/user | owner identity, validated settings, updated_at |
| `narrative_settings` | PK `(chat_id, session_id)` | session FK with cascade; effective preset/custom values |
| `narrative_state` | PK `(chat_id, session_id)` | active scene/thread, story phase, state revision, `updated_through_rowid` |
| `narrative_scenes` | PK `(chat_id, session_id, scene_id)` with scene IDs local to a session | start/end rowids, source revision, thread/viewpoint/status |
| `narrative_threads` | PK `(chat_id, session_id, thread_id)` | status, last scene, source revision |
| `narrative_arcs` | PK `(chat_id, session_id, arc_id)` | status/phase, source revision |
| `director_state` | PK `(chat_id, session_id)` | state revision, accepted-through rowid, active direction/objective, cadence/degraded state |
| `director_decisions` | stable decision ID plus session FK; append order indexed per session | expected story/state revision, source, result, created_at |
| `ending_state` | PK `(chat_id, session_id)` | lifecycle revision, finale-ready revision, checkpoint ID, finale/epilogue operation IDs and committed rowids |
| `ending_goal_history` | PK `(chat_id, session_id, goal_revision)` | story revision, scene ID, source, before/after/reason |
| `narrative_checkpoints` | stable checkpoint ID; unique `(chat_id, session_id, kind, source_revision)` | immutable payload version, source revision, created_at |

Current-state writes use compare-and-swap/freshness predicates on the stored state
revision or `updated_through_rowid`; they are never last-writer-wins. Session-owned
rows reference `sessions(chat_id, session_id)` with `ON DELETE CASCADE`.

The pre-finale checkpoint is immutable and reusable by multiple **separately
requested** Alternate Ending operations. Idempotency applies to one admitted
branch operation, not to the checkpoint itself.

### 30.5 Ending and checkpoints

`ending_state` stores the mechanical ending lifecycle and finale-confirmation
state. It also stores durable recovery identity: the current checkpoint ID,
finale-ready/story revision, accepted finale direction revision, finale operation
ID, committed finale/resolution rowid where present, epilogue operation ID, and
committed epilogue rowid where present. Exact column names may vary, but restart
logic must not infer these boundaries by scanning for an arbitrary assistant
message after the checkpoint.

`ending_goal_history` stores auditable revisions.

`narrative_checkpoints` stores immutable versioned JSON snapshots at the pre-finale boundary. Live canonical state remains normalized; immutable restoration snapshots may use versioned JSON.

### 30.6 Director model selection

The existing task-model resolver already supports arbitrary task names and Utility fallback. Director should use a `director` task route rather than inventing a parallel model-selection system.

Director reasoning gets its own validated setting.

## 31. Migration strategy

Current schema ends at migration 9. This feature uses a forward **Migration 10: Narrative Engine foundation**.

Migration 10 must:

1. create the new tables/indexes/triggers;
2. migrate existing Group Director goals into the canonical Director objective when present, then retire the old `director_goals` runtime source so only one hidden objective remains;
3. backfill every existing session with an explicit Player-centric narrative-settings row;
4. preserve existing Scene State unchanged;
5. avoid AI/provider calls during migration or startup;
6. avoid fabricating historical scenes/arcs for existing transcripts;
7. be idempotent under the repository's migration runner;
8. preserve session delete cleanup/cascade semantics.

After its data is copied successfully, the obsolete `director_goals` table may be dropped in the same migration; no compatibility read/write path should remain.

Old sessions initialize derived narrative state lazily on the next relevant story/Director operation.

## 32. Transactions and concurrency

Never hold a SQLite write transaction while waiting for Story, Utility, Director, image, or embedding providers.

Director flow:

```text
read committed state
      ↓
Director provider call
      ↓
parse + validate
      ↓
short write transaction
      ↓
persist accepted decision
```

Only one accepted Director mutation may advance a given session revision.

If two proposals race from the same revision, the first valid commit wins and the later compare-and-swap/freshness check fails.

Repeated UI clicks must not:

- launch duplicate Director mutations;
- enter finale twice;
- create multiple alternate-ending sessions from one admitted operation;
- apply stale manual edits.

## 33. Validation boundary

Every Director proposal passes through one canonical validator:

```text
model output
  → schema parse
  → NarrativePolicy validation
  → NarrativeState validation
  → revision freshness
  → EndingState validation
  → ACCEPT / REJECT
```

Checks include:

- POV is permitted;
- viewpoint character is valid;
- thread/arc references are valid;
- user-control rules are not structurally violated;
- CLOSED sessions cannot mutate;
- expected revision matches;
- transition type is valid;
- a planned event is not persisted as fact;
- ending transition is legal;
- alternate-ending checkpoint matches the story revision.

Invalid proposals are never partially applied.

The alternate-ending checkpoint check is evaluated against the **originating**
(CLOSED) session and its `session_id`, before the new session exists. The validator
verifies that the stored checkpoint is still valid for that session's committed
revision and that it has not been superseded. Creation of the new session is a
separate durable operation that **references** the already-validated immutable
checkpoint. One admitted operation may create at most one target session, while a
later explicit Alternate Ending request may reuse the same checkpoint to create a
different independent branch. Validator scope and target-session creation never
overlap.

## 34. User-agency enforcement

V1 uses two enforcement layers.

### 34.1 Hard structural rules

The bridge can enforce:

- Director cannot select the user as an autonomous AI speaker;
- Director cannot persist “user decided X” as a state mutation;
- Director cannot mark user thoughts as facts;
- off-screen Light Novel choices cannot masquerade as user actions;
- invalid POV/ownership combinations are rejected.

### 34.2 Prompt semantic policy

Story prompts explicitly forbid invented user dialogue, thoughts, intentions, commitments, emotional conclusions, and consequential actions, while allowing established physical continuity.

V1 does **not** add a second automatic agency-review model call on every response. That would add cost/latency and could reject legitimate prose. A review/repair layer is a future option only if real usage justifies it.

## 35. Closed-session guard

A canonical guard rejects story mutation after `CLOSED`.

Blocked operations include:

- normal story input;
- regeneration;
- edit/continue;
- Light Novel choice submission;
- Director reassessment/mutation;
- Ending Goal mutation;
- all new model/image generation in the closed session, including `/imagine`.

Hard close permits viewing/recovering already committed material, but V1 does not create fresh creative content inside the closed original.

Allowed operations include:

- status/history;
- usage;
- Director/Narrative history;
- View Ending;
- delivery recovery for already committed output;
- Alternate Ending from a valid pre-finale checkpoint;
- New Story.

Blocked operations make zero provider calls.

## 36. Usage attribution

Director and ending operations use distinct purposes so users can see the cost:

- `director`
- `director_reconcile`
- `director_ending`
- `director_epilogue`

Provider-reported usage remains subject to the existing coverage rules. No billing amount or subscription balance is invented.

## 37. Testing strategy

Tests should align with ownership boundaries rather than one monolithic integration file.

### 37.1 NarrativePolicy tests

Verify:

- each preset's canonical mapping;
- World-driven free off-screen behavior;
- Observer cinematic/objective default;
- advanced change → Custom;
- reselect preset → canonical reset;
- `I am not MC` recommendation never toggles it;
- per-user default precedence;
- per-session override precedence;
- old session fallback to Player-centric;
- invalid first-person/user-reserved combination rejected.

### 37.2 `/character` setup tests

Verify exact wizard order:

```text
Character → Narrative Style → Mode → Persona → World
→ System Prompt → Session → Review → Apply
```

Abandoned/cancelled setup persists nothing. Apply persists the session Narrative Style atomically with the rest of setup.

### 37.3 Scene/thread/arc tests

Verify:

- scene creation;
- POV transition;
- thread switch;
- dormant/reactivated/resolved threads;
- multiple threads on one arc;
- committed Story overriding Director intention;
- source revision advancement;
- stale reconciliation rejection.

### 37.4 User-control tests

Verify policy assembly permits physical continuity when already implied and forbids creating new decisions, dialogue, thoughts, commitments, or emotional conclusions.

Tests should validate deterministic policy/structural rules rather than claim they can guarantee every future model output.

### 37.5 Light Novel tests

Verify:

- user-present scene → plausible user actions;
- off-screen World-driven/Observer scene → narrative steering choices;
- no automatic recentering;
- all A/B/C/repair paths receive NarrativePolicy.

### 37.6 Group Director tests

Verify:

- canonical Narrative Director direction constrains speaker selection;
- migrated group goal becomes the canonical Director objective;
- there is no duplicate independent hidden goal;
- user-centering does not reappear in World-driven/Observer.

### 37.7 Director tests

Verify:

- valid proposal accepted;
- malformed proposal gets at most one repair;
- invalid viewpoint rejected;
- invalid transition rejected;
- stale proposal rejected;
- concurrent proposals → one accepted mutation;
- Director provider failure leaves normal Story usable;
- Director model inherits Utility then Story;
- Director reasoning is independent;
- usage is attributed to Director purposes.

### 37.8 Closed Story lifecycle tests

Verify allowed transitions:

```text
OPEN → FINALE_READY
FINALE_READY → FINALE
FINALE → RESOLUTION_COMMITTED
RESOLUTION_COMMITTED → EPILOGUE_PENDING
EPILOGUE_PENDING → EPILOGUE_COMMITTED
EPILOGUE_COMMITTED → CLOSED
```

Verify illegal jumps are rejected, especially direct development/finale-to-CLOSED transitions.

### 37.9 Epilogue recovery tests

Inject failures at:

1. finale model call;
2. finale/resolution commit;
3. reconciliation;
4. epilogue brief creation;
5. epilogue model call;
6. epilogue commit;
7. first Telegram chunk;
8. later Telegram chunk;
9. process restart after commit.

Invariant: never regenerate an already committed resolution or epilogue merely because later delivery failed.

### 37.10 Closed-session tests

Once CLOSED:

- normal text → exact fixed bridge reply;
- mutation commands → blocked;
- Light Novel submission → blocked;
- Director mutation → blocked;
- status/usage/history → allowed;
- delivery recovery → allowed;
- Alternate Ending → allowed only with valid checkpoint;
- blocked action → zero provider calls.

### 37.11 Alternate-ending tests

Verify:

- checkpoint created before finale;
- checkpoint invalidated by pre-finale edit;
- transcript copied only through checkpoint;
- original finale/epilogue excluded;
- narrative state restored at checkpoint revision;
- original stays CLOSED;
- new session has fresh job/delivery identity;
- Hindsight namespace is independent;
- failure during branch creation produces either no new session or a complete new session, never half-state.

### 37.12 Migration tests

Verify:

- Migration 10 creates all required tables;
- old sessions remain Player-centric;
- no provider calls occur during migration;
- Scene State remains untouched;
- Group Director goals migrate safely;
- migration is idempotent;
- deletion cleans narrative-owned rows;
- upgrade from supported schema baselines succeeds.

## 38. Performance and cost guardrails

The behavior must be testable.

Normal Player-centric turn with no Director trigger:

```text
1 Story call
0 Director calls
```

Triggered Director reassessment:

```text
1 Director call
1 Story call
```

Stable turns below cadence threshold:

```text
0 additional Director calls
```

Closed session normal message:

```text
0 provider calls
```

Failed Telegram delivery after committed ending:

```text
0 new Story calls
delivery recovery only
```

Background reconciliation stays bounded and should reuse existing background-executor ownership rather than create unbounded workers.

## 39. Failure invariants

The implementation must preserve these invariants:

- a model plan cannot become a fact without committed Story evidence;
- stale model output cannot mutate current state;
- a failed Director cannot silently change Narrative Style;
- a failed Story call cannot advance scene/arc/ending reality;
- a failed reconciliation cannot erase a committed reply;
- a failed epilogue cannot regenerate an already committed resolution;
- a Telegram delivery failure cannot reopen a CLOSED story;
- a process restart cannot reopen a CLOSED story;
- Alternate Ending cannot mutate the original;
- no ordinary closed-session message contacts a provider;
- no migration/startup path makes surprise provider calls;
- no long-lived SQLite write transaction spans external I/O.

## 40. Incremental implementation strategy

The architecture is Approach 3, but implementation should be incremental.

### Phase 1 — Narrative Engine foundation

- Migration 10 and repositories;
- NarrativePolicy/defaults/settings;
- `/character` Narrative Style setup;
- Narrative panel and status;
- explicit scene/thread/POV state;
- revision-aware reconciliation;
- Story/Light Novel/Group policy consumption;
- user agency rules.

### Phase 2 — Canonical Director

- dedicated Director model/reasoning route;
- structured Director proposals;
- validation;
- adaptive cadence;
- Director Room;
- current Group Director goal integration;
- decision history.

### Phase 3 — Arcs and Closed Story

- arc tracking;
- Ending Mode/Goal/history;
- finale readiness;
- pre-finale checkpoint;
- strict ending lifecycle.

### Phase 4 — Separate epilogue and closure

- resolution reconciliation;
- epilogue brief;
- separate Story epilogue;
- CLOSED guard;
- restart/delivery recovery.

### Phase 5 — Alternate Ending

- checkpoint restore into new session;
- transcript/local-derived-state copy through boundary;
- Hindsight isolation/seeding;
- immutable original.

Each phase must ship with its own regression coverage and preserve the architectural contracts in this specification.

## 41. Documentation changes required with implementation

When implementation ships, update:

- README feature summary;
- User Guide first-conversation flow;
- Narrative Style section;
- Light Novel behavior;
- Group Director behavior;
- Closed Story/epilogue/Alternate Ending;
- Configuration/provider model selection for Director;
- token usage coverage;
- Operations recovery semantics;
- `/help` command/detail catalog;
- Mini App documentation.

Do not document future phases as already available before their implementation ships.

## 42. Final decisions captured

The approved design choices are:

- Hybrid presets + Advanced controls.
- Per-user default plus per-session override.
- Player-centric is the compatibility/default preset.
- World-driven and Observer allow free off-screen storytelling.
- Physical continuity is the default user-control policy.
- World-driven defaults to rotating third-person limited.
- Observer defaults to cinematic/objective.
- Player-centric defaults to user-anchored third-person limited.
- Ensemble defaults to rotating third-person limited.
- `I am not MC` remains independent and is only recommended for World-driven/Observer.
- Any Advanced change marks the style Custom.
- Narrative Style applies to Story, Light Novel, and Group Director.
- Approach 3 is the long-term architecture.
- Director Room is hidden by default but revealable/editable.
- AI Director is constrained autonomous.
- Closed Story uses optional/adaptive Ending Goals.
- Ending Goal adaptations are recorded in revision history.
- Finale entry is autonomous by default with an optional confirmation requirement.
- Story closure is hard after a separate epilogue.
- Alternate Ending creates a new session from a pre-finale checkpoint.
- Epilogue is a separate Story generation.
- Epilogue time scope is selected by the Director within established consequences.
- Director gets its own optional model route and reasoning setting.
- Director cadence is adaptive with Advanced fixed/custom override.
- Narrative Style is selected immediately after Character in `/character`.
- Personal default is per Telegram user, never bot-wide.

## 43. Approval and implementation gate

This document records the approved conceptual design. It does **not** authorize implementation by itself.

Before coding:

1. the user reviews this committed design specification;
2. requested design edits, if any, are incorporated and self-reviewed;
3. the user explicitly approves the written spec;
4. a separate implementation plan is written and reviewed;
5. only then does implementation begin.

