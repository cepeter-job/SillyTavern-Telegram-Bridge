# User guide

[Back to README](../README.md) · [Installation](installation.md) ·
[Configuration](configuration.md) · [Troubleshooting](operations.md#troubleshooting)

Start with [Your first conversation](#your-first-conversation). You can leave
optional features alone until you have exchanged a few messages with a character.
The remaining sections are references to return to as you need them, not a setup
checklist you must finish before chatting.

Commands beginning with `/` go in Telegram. Terminal commands in the installation
and operations guides run on the Linux host instead.

- [Your first conversation](#your-first-conversation)
- [Everyday controls](#everyday-controls)
- [Sessions and recovery](#sessions-and-recovery)
- [Models, context and memory](#models-context-and-memory)
- [Story trackers and checks](#story-trackers-and-checks)
- [Narrative Style](#narrative-style)
- [Director Room](#director-room)
- [Closed stories and alternate endings](#closed-story-and-the-epilogue)
- [Light Novel choices](#light-novel-mode)
- [Characters and native data](#characters-and-native-data)
- [Images, voice and documents](#images-voice-and-documents)
- [Live Sync and groups](#live-sync-and-groups)

## Your first conversation

After [installing the bridge](installation.md), open your bot's private chat.
For this first test, use **Player-centric**, **Normal** and the configured Story
model. You can explore other styles and helper models afterward.

1. Send `/character` and choose a PNG character card.
2. Choose a **Narrative Style**. **Player-centric** keeps the focus on your
   character. **Ensemble**, **World-driven** and **Observer** let the story follow
   a wider cast or events away from you. Then choose **Normal** or **Light Novel**;
   Light Novel adds an A/B/C strategy selection.
3. Select an optional Persona, World Info and System Prompt. **Off/Skip** is
   available, and you can select more than one lorebook.
4. Choose an unstarted session or create a named one, then tap **Apply**.
5. Open `/providers` and check the **Story** model. This model writes the replies.
6. Send `/start`, preview the character's Default or Alternate greetings, and
   choose one. Then send your first message.

You should now see the character's opening message. Try a short reply such as:

```text
*I pause at the doorway.* "Were you expecting someone?"
```

If the character answers, the basic setup works. Use `/status` to check the
active session and `/session` when you need to return to it later. If no answer
arrives, check [Troubleshooting](operations.md#troubleshooting) before sending
several retries.

Setup is a draft until you tap Apply. Applying setup does not start the story;
`/start` sends the card's opening message. That greeting does not need a Story
generation request. Light Novel mode then makes a separate request for its first
choices.

A new or reset standard session answers conversational input with
`Please use /start command.` until you choose its greeting. Setup commands still
work. After a session starts, `/start` reports that it has already started. To try
a different opening, create another session or confirm a reset.

### A few terms

| Term | What it means here |
|---|---|
| **Character** | A card describing a character or cast for the model to play. |
| **Persona** | The identity and description you use for your own role in the story. |
| **World Info / lorebook** | A collection of setting or character facts that can be added to the prompt. |
| **System Prompt** | Instructions that guide the model's responses. |
| **Session** | One story, with its own message history and selected settings. |
| **Provider** | The service the bridge contacts to run a model. |
| **Route** | A provider and model together, written as `provider-id::model-id`. |
| **Story model** | The model that writes the roleplay. |
| **Utility model** | The model used for summaries, some choice strategies and other helpers. |
| **Director model** | The model that plans scene direction and endings, rather than writing the ordinary reply. |
| **Inherit** | Use the model selected for another role instead of choosing a separate one. Utility can inherit Story; Director can inherit Utility. |
| **Context window** | How much text a model can handle in one request, measured in tokens. It is not the size of your saved story. |
| **Native data** | SillyTavern's existing cards, lorebooks, prompts and Persona files. Edits to these files can affect other stories that use them. |
| **Thread / viewpoint** | A thread is a storyline being followed; a viewpoint is the character or perspective through which a scene is told. |

You do not have to memorize these terms. The relevant command or page is linked
from each workflow below.

## Everyday controls

Most commands open a panel with buttons. Tap once and let the action finish.
When a panel asks for text, your next message is treated as that answer rather
than story dialogue; send `/cancel` to back out first.

An expired panel is usually just an old set of buttons. Reopen the command to
get controls for the current session. Switching sessions does not make an old
confirmation apply to the new story.

| When you want to… | Use |
|---|---|
| Start a fresh story | `/new`, then `/character` and `/start` |
| Switch between existing stories | `/session` |
| Check the active session and context budget | `/status` |
| Choose Story, Utility or Director models and reasoning | `/providers` |
| Choose point of view, cast focus and off-screen freedom | `/narrative` |
| Adjust sampling, reply length or optional style controls | `/settings` |
| Enable or disable live reply previews | `/stream` |
| Change the reply language or save generation settings | `/language` or `/preset` |
| Change the Persona, lorebooks, prompt or Author's Note | `/persona`, `/world`, `/systemprompt`, `/note` |
| Inspect the assembled prompt and context budget | `/prompt` |
| Record a d20 result for an explicit action | `/check stealth 12 cross the courtyard` |
| Find every command and its accepted arguments | `/help` |

`/help` is the canonical command reference. Try `/help databank search` or
`/help scene refresh` for one action. Some typed forms act directly, while
others open a panel: for example, `/language English` sets a language, but
`/stream on` still asks you to use the panel buttons.

### Narration and dialogue

Replies use italic actions/narration and normal spoken dialogue. For example:

> *She turns toward the doorway.* "I heard something outside."

The bridge normalizes reply formatting before saving and sending it. It also
formats opening greetings while preserving valid card-authored action markup
and unquoted dialogue. Inline and fenced code are protected from this formatting.

For your own messages:

```text
*I open the door slowly.*
"Is anyone there?"
```

Single-star spans tell the model that you are describing an action. Your original
input remains unchanged in the stored transcript. Double-star text is not an
action marker.

If you enable automatic voice, only dialogue inside **straight double quotes**
is queued for speech. Curly quotes, actions and unquoted text remain text-only.
This applies to both your messages and character replies.

## Sessions and recovery

### Keep separate stories

`/new` asks for a name of 1–80 characters and switches to the new session. It
keeps the previous story and its Telegram messages. `/session` lets you return
to that story later.

Each session keeps its transcript, character setup, Story/Utility/Director selections,
reasoning and generation settings, response language, variants, notes, summary
and derived memory. Queued work stays attached to the session that submitted it;
switching sessions does not redirect an unfinished reply or edit.

### Retry, regenerate or continue?

| Situation | Action | Does it make a new model request? |
|---|---|---|
| A response failed, or a saved reply was not fully delivered | `/retry` | A generation failure may need one; valid saved-output recovery reuses the answer. |
| The answer arrived, but you want a different version | `/regen` | Yes. It creates another response variant. |
| You want to choose an existing version | `/swipe` | Selecting a stored variant does not generate a new one. |
| You want to change the active response branch | `/branch` | It selects an existing branch. |
| The answer stopped and you want more | `/continue` | Yes. |
| You want to replace your latest user message | `/edit` | Yes, after you submit the replacement. |
| The story arrived but Light Novel choices are missing | **Retry Choices**; `/lightnovel` reopens the panel | It retries the choices, leaving the saved story in place. |

For a failed turn, return to the original session before using `/retry`. When
the answer has already been saved, automatic delivery recovery has three total
attempts, including interrupted attempts across restarts. It skips Telegram
chunks whose acknowledgements were recorded. A deleted or replaced answer can
no longer be recovered.

Occasionally Telegram may receive a message just before the bridge crashes,
leaving the bridge unsure whether it arrived. Recovery can then produce a
duplicate. Check the chat before retrying again; the bridge cannot promise
exactly-once delivery through every interruption.

### Streaming and long replies

With streaming enabled, a temporary preview updates during generation. The bridge
removes it when the completed reply is ready and splits long messages at natural
breaks within Telegram's length limit.

If the provider reports an output-token limit, the bridge can make up to three
automatic continuation requests. These can consume additional tokens. `/continue`
is available when you deliberately want another segment. Light Novel strategy A
and Humanizer have different preview behavior, described below.

### Reset or delete a session

`/reset` opens a confirmation before clearing the active conversation and its
memory. It keeps the session and selected Character, mode, Persona, World,
System Prompt and Narrative Style, then waits for a new opening greeting. Send
`/start` again. Do not use Reset just because a reply or choice panel failed;
try the matching recovery action first.

Reset clears variants, failed turns, summaries, curated/episodic memory, NPC state
and old choices, including tracker values and recorded checks. It also clears narrative scenes, threads and derived planning
state, without changing your saved personal defaults. It attempts to remove
tracked Telegram conversation messages;
Telegram may refuse old messages or deletions without sufficient permissions.

Delete an **inactive** session through `/session`. Sessions with running or
queued work cannot be deleted. When required Hindsight cleanup cannot be
verified, destructive local cleanup is refused. Other sessions are unaffected.

## Models, context and memory

### Choosing models

Use `/providers` to choose Story, Utility and Director independently for the current
session. Story writes the prose. Utility handles summaries and extraction. Director
plans the next scene. You can start with one model for all three roles: leave
Utility and Director inherited rather than choosing a separate model for each.
An unset Director model inherits Utility, then Story.

Both Story and Utility reasoning are configured from `/providers`. Director has
its own reasoning control there and in Director Room. Reasoning is the model's
additional thinking work; higher budgets can increase waiting time and token use.
Reasoning support depends on the provider; a zero budget can mean the backend
default rather than disabled reasoning.

The provider list also offers **Provider health** and **Refresh models**. Manual
inference probes may use quota. **Reset runtime** clears the bridge's local
cooldown so another request can be tried; it does not fix credentials, add credit
or change your selected model. See
[provider diagnostics](configuration.md#provider-diagnostics-and-catalog-maintenance).

### Long conversations and context limits

Open `/prompt` → **Budget** to see the resolved context window, output reserve,
safety margin, estimated input budget and what was reduced in the last prompt.
`/status` already includes a compact input/window/compaction summary. These are
planning estimates; billed usage comes from provider counters.

The bridge keeps recent history and reduces older history or optional retrieved
context when a prompt grows too large. It preserves the current user turn and
fixed character/system instructions. If those fixed parts still cannot fit, it
refuses the request before calling the provider and explains what to shorten.
The saved transcript remains available even when older turns are omitted from
the next prompt.

Operators can keep a pinned model list with `discover_models: false` and enable
`discover_model_metadata: true` to learn context sizes for those models. Explicit
configured context values override discovery. Unknown sizes use the configured
fallback, 32K by default; this is a planning fallback, not proof that every model
accepts 32K. See [context configuration](configuration.md#context-planning-and-diagnostics).

### Choose the memory tool for the job

| Tool | What it keeps | Where to manage it |
|---|---|---|
| Continuity summary | A condensed account of the current story | `/summarize`; Mini App Memory |
| Hindsight recall | External long-term facts for the active session | `/memory`, `/remember` |
| Curated memory | A small editable list of durable facts | `/memory curated`; Mini App Memory |
| Episodic memory | Local durable events extracted from completed summary segments | Recalled automatically when relevant |
| NPC Bank | Supporting-character descriptions, relationships, goals and field history | `/npc` |
| Data Bank | Uploaded reference documents | `/databank` |

Hindsight is optional and requires a configured service. Curated facts can be
edited locally in the Mini App; publishing the reviewed list to Hindsight is a
separate action. Ordinary summaries, episodic records and NPC state are local
derived data. Their extraction/refresh work uses the configured Utility route.

NPC and episodic records can restrict knowledge to named characters using
`known_by`. The active character sees only permitted fields. NPC field history
offers Undo for the latest visible revision; a newer update makes an old
confirmation stale. Editing earlier conversation invalidates derived events and
rolls NPC state back to the applicable revision before regeneration.

### Model calls and token use

| Feature | Extra work to expect |
|---|---|
| Normal reply | A Story request; retries, continuation or language rendering can add calls. |
| Light Novel A | Usually story and choices together; the greeting needs a choice request, and invalid choices can need repair. |
| Light Novel B / C | A separate choice request after the story, using Utility / Story respectively. |
| Humanizer | An additional prose rewrite using the reply's model. |
| Summaries, memory/NPC/scene refresh, optimizer and ranking | Utility-model work. |
| Story tracker extraction | Shares the existing NPC Utility call; upgrading older sessions can replay available history. |
| `/check` | A local d20 roll and saved receipt, with no model request. |
| Narrative continuity | A bounded Utility reconciliation after committed replies; older or edited history can need more than one batch. |
| Current Scene image | Utility preparation of the visual prompt, then an image-provider request. |
| AI Director | One planning call on an event or cadence threshold, with at most one repair for malformed version-1 output. Groups reuse the accepted plan without another planning call. |
| Autonomous groups | Bounded multi-character Story replies; not another independent story planner. |

Review reported counts in the Mini App's **Manage → Advanced settings → Usage**.
A failed request can still consume tokens. The tracker is not an invoice or a
remaining-quota display, and some task types are outside its coverage. See
[Token usage](token-usage.md).

### Optional style controls

**I am not MC** in `/settings` is the Grounded User option. It is off by default.
It asks the model to respect established abilities, obstacles and NPC motives,
without inventing special treatment for the user or unfairly weakening them.
Explicit Persona advantages still apply. It adds instructions to the existing
prompt; it does not make a separate rewrite request.

**Humanizer** is also off by default. It rewrites completed prose after language
rendering and adds latency and token use. With it enabled, ordinary replies do
not show the raw streaming preview. Replies over 24,000 characters bypass the
rewrite; requests are capped at 4,096 output tokens and use a 30-second
per-request timeout. Recovery can add requests, so this is not a whole-turn
deadline.

If the rewrite fails or changes protected fragments such as code, numbers,
dialogue or links, the original rendered reply is kept. These checks cannot prove
that meaning is unchanged. There is no automatic weekly reference refresh or
prompt promotion. Attribution remains in [Third-party notices](../THIRD_PARTY_NOTICES.md).

## Story trackers and checks

The bridge keeps established story mechanics alongside canonical NPC and plot
state. NPC Utility extraction reads committed prose and updates bounded tracker
records; it does not need the Story model to append an Internal States ledger.
Background extraction can finish after the visible reply, so newly established
facts become available once that work is accepted.

| State | How it is used |
|---|---|
| NPC relationships | BOND, Sparks and Grudge for supporting NPCs, matched to canonical names and unambiguous aliases. Eligible NPC Bank relationship fields show the current attitude tier. |
| NPC agendas | Bounded objectives, steps and status. An active offscreen agenda advances once per accepted assistant turn unless that source supplies explicit progress. |
| User inventory, skills and conditions | Established possessions, abilities and conditions, including domain-specific check modifiers. Proposed actions do not grant an item, skill or reward. |
| Factions | Established goals, relationships, morale, conflict and private intelligence. |
| Quests and foreshadowing | Progress and descriptive metadata linked to existing Narrative arcs or threads when available. Native Narrative state determines linked plot status. |

NPC Bank retains identity, editable fields and field history. Automatic tracker
projections preserve fixed fields, private fields and manual overrides. Scene
state continues to own location, weather and physical participants; Narrative,
Director and Ending continue to own plot progression and closure. A completed
agenda counter alone does not establish an unseen consequential scene.

When NPC Bank later establishes a fuller name and an unambiguous alias, prior
tracker values follow that identity. Earlier story views retain the old name.
Conflicting values under two names stop that extraction publication until the
source or NPC identity is corrected; scores are never added together by guesswork.

Private agendas, faction intelligence and future payoffs are narrator data.
Character-scoped prompts do not receive other characters' private trackers.
For a shared group prompt, every reader must be authorized; an unresolved member
does not grant access to another character's agenda.
Wider narrator viewpoints can use these facts without granting characters new
knowledge. Tracker context shares the normal optional-context budget and can be
trimmed when the request needs space.

### View saved trackers

Use `/trackers` in Telegram, or open **Manage → Story trackers** in the Mini App.
The view shows saved relationships, visible agendas, inventory, skills,
conditions, faction state, linked quests and recent d20 checks for the active
session. It also reports how far accepted extraction has caught up with the
conversation. An empty view can mean no tracker facts have been established yet,
or that background extraction is still catching up.

Opening or refreshing this view reads saved state. It does not call a model,
roll another check, advance agenda timers or add a story turn. NPC visibility
rules apply; private intelligence and future plot payoffs are omitted. Use
Director Room to inspect the plans it exposes. Telegram shows a bounded summary;
use the Mini App when you need the fuller list. `/status` continues to describe
the active session and its configuration.

Trackers remain in the bridge's **SQLite database** alongside source receipts,
revisions and checks. That keeps updates and rewinds consistent with the saved
conversation. Separate JSON or text files would duplicate the state and need
their own locking and recovery rules. JSON is useful for an API response or an
export; it is not an additional source of truth for these trackers. This view
requires no new database migration.

### Relationship mechanics

BOND ranges from **−5 to 20**: hostile through −3, neutral through 2, warmth through
7, trust through 15, then love. Established events can change Sparks by at most
2 and Grudge by at most 1 per source; direct BOND changes can only lower it, by
at most 2. An established apology clears Grudge.

Every third assistant turn, Grudge of at least 5 lowers BOND by 1 and resets
Grudge; smaller positive Grudge decays by 1. Every fifth assistant turn, at least
7 Sparks increases BOND by 1 and resets Sparks; the threshold is 14 while Grudge
is at least 3. Otherwise Sparks decays by 1 unless that source added Sparks.
Replaying the same source or processing a user message never advances these
timers again.

### Make a check

Use an explicit domain, difficulty and attempted action:

```text
/check stealth 12 cross the courtyard unseen
/check social 15 ask Maya about the Silver Key
```

DC must be a whole number from **1 to 20**. The domain is one word, up to 40 UTF-8
bytes; the action can contain spaces, up to 500 UTF-8 bytes. The bridge records
the user action and rolls d20 once. Established inventory, skills and conditions
for that domain, or `any`, each contribute from −2 to +2; the combined modifier
is bounded to −6 through +6.

| Result | Rule |
|---|---|
| Critical failure | Natural 1, or total minus DC at most −8. |
| Critical success | Natural 20, or total minus DC at least +8. |
| Success | Total meets or exceeds DC. |
| Near miss | Total is 1–3 below DC. |
| Failure | Total is 4–7 below DC. |

Natural 1 and 20 take precedence. The receipt shows the roll, modifier, total,
DC and outcome. Send your next story turn to narrate the consequences using
that recorded result. A check does not itself generate prose or advance NPC
agenda timers. In a manual-turn group, only the current actor may admit a new
check.

Delivery retries and `/retry` reuse the saved result. A new `/check` message is
a new action and roll. Editing or replacing its source invalidates the old
result; reset removes session checks. Alternate endings restore independent
copies of eligible checks and tracker state from their checkpoint.
Checkpoints preserve the tracker revisions needed for historical reads and
rollback. A checkpoint that exceeds its bounded history/size limit is rejected
instead of saving a partial history.

### Existing prompts and older sessions

The bridge filters recognized legacy tracker-output sections from the prompt
copy sent to models. Your stored System Prompt, Author's Note and character
card remain editable as before. Reserved `<internal_states>` blocks are removed
from previews, new story output and assistant-history prompt copies; ordinary
Telegram spoilers remain visible through their normal spoiler formatting.

After migration 25, existing NPC work replays available committed sources to
backfill trackers. Exact historical records such as
`Maya: BOND=8 Sparks=3 Grudge=2` inside an assistant Internal States block can seed
a missing numeric baseline. Unproven numbers and unsupported legacy layouts are
left unset. Historical transcript rows remain intact; the bridge does not save
a second hidden assistant response for future turns.

## Narrative Style

Open `/narrative` to choose how the **current story** is told. You also choose
this style immediately after selecting a character in `/character`.

| Preset | What changes |
|---|---|
| **Player-centric** | Third-person limited, anchored to your character. Off-screen scenes are rare and brief. |
| **Ensemble** | Limited viewpoints rotate between cast members. Cutaways are allowed but bounded. |
| **World-driven** | The world and its cast can carry the plot. Scenes may stay away from your character without a forced return. |
| **Observer** | Cinematic, externally observable narration. You can follow the story from outside it and intervene when you choose. |

For example, a World-driven story can follow a guard at a distant gate while
your character remains elsewhere. The guard does not need to bring every event
back to you. This does **not** give the model permission to decide what your
character says, thinks, promises or chooses.

All four presets use **Physical continuity**: the model may connect an action
you have already chosen with harmless movement, but your dialogue, thoughts,
commitments, emotional conclusions and consequential decisions stay yours.
**Advanced** lets you change point of view, your role, focus, cutaways and user
control separately. Changes that differ from a preset appear as **Custom**.
First-person narration must follow an AI-controlled viewpoint, not invent your
character's inner monologue.

**Save as my default** only prefills your future character setup. It does not
change existing stories. Choosing a style during setup is still just a draft
until **Apply**. Changing a session's style invalidates its old Light Novel
choices, so an outdated menu cannot act under the new rules.

**I am not MC** is a separate option in `/settings`. World-driven and Observer
recommend it, but never turn it on automatically. Narrative Style controls
how the story is told; Grounded User controls assumptions about your character's
abilities and importance.

`/status` shows your style, point of view and current scene/thread. A **stale**
narrative record means the helper record has not yet caught up with the latest
saved messages or settings; it does not mean your messages were lost. The record
describes saved story facts, not proposed future events. It is separate from
`/scene`, which tracks
physical surroundings and continuity. Updating the narrative record in the
background is called reconciliation. It uses the Utility model and is reported
as `director_reconcile` usage. Opening a style
panel or status page makes no model request. If reconciliation fails, your
saved story stays intact and generation keeps the selected policy rather than
pretending stale facts are current.

## Director Room

Open `/director`, or **Manage → Director Room** in the Mini App, to see what the
Director is planning. This is a private planning view: plans do not become story
facts, and characters do not learn them just because you opened the panel.

The room shows the current scene, viewpoint, storyline (thread), accepted
direction and recent decisions. You can read the plan without turning it into
something that has happened in the story. **Reassess now** asks the configured
Director model for a fresh plan.
It can use provider quota. **Choose thread** plans a future scene without rewriting
anything already committed.

Use **Next scene only** for a temporary instruction such as “Stay with Mara at the
gate.” It expires when the scene changes or its source history/settings become
invalid. A **Persistent objective** stays active until you change or clear it. The
AI Director cannot silently remove that objective. Both forms still reserve your
character's dialogue, thoughts and consequential decisions for you.

**Adaptive** cadence is the default. Stable/setup scenes allow up to 10 completed
Story turns between checks, development 6, escalation 4 and climax 2. Meaningful
scene or thread changes can trigger a check sooner. Advanced controls offer fixed
4/6/10-turn intervals or a custom whole-number interval from 1 to 100. Ordinary
turns below the threshold make no extra Director call.

A Director outage does not change your Narrative Style or erase a reply. The bridge
keeps valid guidance and otherwise continues conservatively from committed state.
Stale or malformed plans are not applied. The Mini App and Telegram controls reject
edits from an old panel rather than silently overwrite a newer decision.

## Closed Story and the epilogue

Choose **Closed Story** in the Advanced step after selecting a character, or open
**Director Room → Ending settings** for an existing story. The default remains
Open-ended. An optional Ending Goal gives the Director a destination without
scripting your character's decisions. Its revisions and reasons remain visible.

A finale starts automatically when current story evidence supports it. Turn on
**Ask before finale** to review the Director's reason and press **Begin finale**
yourself. Continuing the story first expires that old confirmation. The bridge
saves an immutable pre-finale checkpoint before entering the finale.

The finale can span several turns. Once the resolution has been saved and
the bridge has updated its story record, the Director prepares a brief and the
Story model writes a **separate
epilogue**. Its time jump may show the immediate aftermath or a later future, but
must preserve your agency and deliberately unresolved facts.

A completed story is read-only. New story messages receive:

> This story has ended. Please start new story.

Editing, regeneration, choices, `/reset`, new images and other creative work cannot
reopen the original. Status, history, usage and Director Room remain available.
Choose **New Story** or `/character` to start a different session.

When an epilogue or its delivery is interrupted, use `/retry` or **Recover saved
ending**. The bridge retries the unfinished stage, not the already committed
resolution or epilogue. A Telegram outage does not undo a saved ending. A provider
failure pauses automatic retries to avoid repeatedly spending tokens.

### Try an alternate ending

Open the completed story's **Director Room → Ending settings** and choose
**Alternate Ending**. The Mini App offers the same action. It appears only when
the original has a valid saved pre-finale checkpoint.

The bridge creates a separate, already-started session named after the original
with “— Alternate Ending” added. It copies the story only through the checkpoint,
along with the character setup, models, preferences and local continuity as they
were then. It does **not** copy the original finale, epilogue or their later facts.
Continue the new session normally to explore another resolution.

The original stays closed. Returning to it later and explicitly requesting another
alternate ending creates another independent session. Repeated delivery of the
same button action returns the same branch instead of making accidental copies.

The new session keeps its external memories separate from the original ending.
If Hindsight cannot receive the copied history, the app reports a memory warning.
Your new local story is still there: you can use its copied transcript and local
continuity without borrowing memories from the original finale. Later memory
updates use the new session's identity.

`/branch` still selects existing response variants; it is not Alternate Ending.
A finale freezes its earlier story history, so `/reset` cannot discard that
checkpoint after the finale begins. Start a new story instead.

## Light Novel mode

Choose Light Novel during `/character` setup. `/lightnovel` changes its strategy
while the session is unstarted, or restores the current choice panel during a
story. Use a new session or reset before changing an already-started mode.

| Strategy | How it works | When to consider it |
|---|---|---|
| **A — Story Inline** | Story and choices come from one structured Story response. | Fewer routine requests, if the model follows the format reliably. |
| **B — Utility Model** | The Story model writes prose; Utility makes the choices. | Separate the writing model from the helper model. |
| **C — Story Second Pass** | The Story model writes prose, then receives a second choice request. | Use the same model for both tasks without inline structured output. |

All strategies make a choice-only request after the card-authored greeting.
Strategy A hides raw structured streaming and can run one automatic choice-only
repair when usable prose arrives without valid choices. B/C retain ordinary story
streaming when other settings allow it.

The panel offers 3–4 full-text choices with numbered buttons. When your character
is present, these can be in-world actions. Off-screen scenes instead offer
narrative steering, such as following another thread or cutting to a new scene.
**Next Scene** respects Narrative Style: World-driven and Observer do not force
a return to your character. It never chooses dialogue or consequential actions
for you. You can always type your own reply;
doing so invalidates the old choices. Double taps and stale buttons cannot submit
another committed user turn from the same choice.

If the story arrived but the choices did not, tap **Retry Choices** once. It
requests choices for the saved story; it does not rewrite that story. Reopen
`/lightnovel` when the panel itself is missing. You can also type your own reply
instead of waiting for choices. A retry can still consume model tokens.

Choice context differs by strategy. A sees the assembled Story prompt. B/C and
A's repair pass use a bounded snapshot of Character, Persona, relevant World
Info, System Prompt, Author's Note, summary, NPC state, recent messages and the
current story. That separate pass does not resend post-history instructions,
Hindsight/episodic recall or Data Bank context. Grounded User instructions also
apply to choice generation when enabled. Every strategy also receives the
current Narrative Style and only uses scene facts that are current for that
committed response.

Normal mode makes no choice requests. Light Novel applies to standard sessions;
Forum Topic groups use their own turn controls.

## Characters and native data

### Uploading or editing a character

Use `/character` to browse cards, inspect their information or open the Optimizer.
Send a character PNG as a Telegram **Document/File** so its embedded SillyTavern
metadata is preserved. A portrait sent as a compressed photo is not a card upload.

A new validated card is installed with a verified backup. If the filename already
exists, the bridge offers **Overwrite**, **New version** or **Keep existing**.
Review that choice before confirming. Active/default cards and cards referenced
by sessions or groups are protected from deletion.

For model-assisted editing:

1. Open **Optimizer** and choose **Auto Optimize** or **Manual Suggestion**.
2. For Manual Suggestion, describe the change in up to 2,000 characters, for
   example: "Shorten the first message and keep the backstory."
3. Review every proposed field. **Revise** works from the temporary preview.
4. Tap **Apply** to save, or discard the proposal to keep the installed card.

Telegram proposals expire after ten minutes and belong to the originating user,
session and card revision. Apply makes a backup and verifies that the installed
card still matches the preview's starting point. If the card changes meanwhile,
generate a fresh preview. An interrupted application may also require a new one.

The optimizer preserves card identity and unrelated metadata, and validates
embedded `chara`/`ccv3` copies together. It is still a model-assisted editor:
review system prompt and post-history instruction changes as carefully as prose.
Invalid previews leave the installed card unchanged.

S–D rank badges are model opinions, not objective quality scores. Ranking and
optimization send card text to the Utility provider and can use tokens. Applied
changes request a new rank; an unavailable rank leaves the card usable.

New ranks are tied to the card's content, so copying unchanged bytes to another
data directory does not by itself remove the rank. Older records used a file's
location and filesystem details. The bridge can upgrade those records only when
the original recorded file still proves that the card is unchanged. If that
original is gone, rerank the current card rather than editing the database to
force a stale badge back into view. The
[rank asset manifest](../assets/character-ranks/README.md) records the badge artwork.

### Personas, Worlds and prompts

`/persona` uses native SillyTavern Persona settings and avatars. A new Persona
requires an existing native avatar; fresh installations include a starter avatar.
The active Persona and those referenced by other sessions cannot be deleted.
If none is selected, the bridge tries the native default or a sole available
Persona, then falls back to a generic label.

`/world` selects one or more native lorebooks. Telegram imports accept World Info
JSON in `{ "entries": { ... } }` form, up to 10 MB, without overwriting an existing
filename. The Mini App editor has its own smaller limits. In-use worlds are
protected from deletion.

`/systemprompt` selects native JSON or TXT files in the configured `sysprompt`
directory. JSON uses `name` and `content`; native `post_history` fields are
currently ignored. `/note` sets a session Author's Note. Menus and status show
prompt labels rather than their bodies. Paths and example configuration are in
[Configuration](configuration.md#paths-and-native-sillytavern-data).

`/expression` controls automatic or manual sprites. If a selected sprite is
unavailable, the bridge tries neutral, then the character avatar, then text-only.
Automatic expressions are sent only when the effective expression changes.

`/macro` previews supported macros. `/stscript` exposes a limited set of bridge
actions, including confirmed reset; it cannot run arbitrary shell commands,
filesystem operations or network requests.

## Images, voice and documents

### Generate an image

Configure an image provider first, then open `/imagine`:

- **Current Scene** uses the latest saved assistant turn and structured scene
  state to prepare a visual prompt with Utility, then calls the image model.
- **Custom Prompt** asks you to type a one-off image description.
- **Realism / Anime** chooses the visual style. The checkmark shows the saved
  selection; Realism is the default. It applies to both generation buttons.
- **Options** selects the session's image model and size preference.

If the catalog supplies valid text and reference targets, **Auto** uses the active
character PNG as one visual reference when available; otherwise it uses the text
model. The PNG is read in memory when you generate. A manually selected model
overrides Auto, and a manually selected reference model needs a usable PNG.

Size presets are Square (`1024x1024`), Landscape (`1536x1024`) and Portrait
(`1024x1536`). A reference model may use provider-controlled sizing instead of
the exact preset. Reset selects Auto when configured, or the provider's first
image model, and restores Square and Realism. The style belongs to this session
and is included when an alternate-ending session copies its image preferences.

Style selection guides both the visual prompt and the final image request. A
character reference preserves identity while the selected style controls the
medium. The selected model's capabilities still affect the result. Custom Prompt
shows the available input length after reserving space for style and reference
instructions, so that overhead is included in the provider's prompt limit.

Generation leaves the story transcript unchanged. Successful delivery contains
only the image, with no source or revised prompt caption. A provider failure
does not automatically try another image model. Typing `/imagine <text>` opens
the panel; it does not bypass it.

Photos with captions can be analyzed by a vision-capable Story model. If the
selected model does not support vision, the bridge refuses the turn without
changing the transcript.

### Voice

`/voice_input` configures Faster-Whisper transcription and its model/language.
Transcription runs in the background, then enters the session as a text turn.
Local speech models need additional resources when used.

`/voice` enables spoken dialogue through the configured TTS tool and voice.
Use straight double quotes, as described under [narration and dialogue](#narration-and-dialogue).
There is no `/tts` command. See [voice configuration](configuration.md#voice)
if the bot reports a missing voice or executable.

### Documents and Data Bank

The Data Bank accepts PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML and XML.
Upload as a Telegram Document or through the Mini App. Character PNGs follow the
separate card-validation flow.

Use `/databank` to search, review versions, activate an older version, remove a
filename's versions, or reindex. Documents belong to the bot chat, so sessions in
that chat share this library. Full-text search works without an embedding service;
semantic search needs [embedding configuration](configuration.md#data-bank-semantic-embeddings).
Reindex after changing embedding model, dimensions or revision.

## Live Sync and groups

### Live Sync

Live Sync is off until a SillyTavern loopback API is configured. `/sync` reconciles
the selected session before enabling realtime updates. Conflicts, authentication
failures or invalid responses stop sync for review.

It uses the Live API, not chat-file polling or JSONL import/export. The bridge
does not install SillyTavern extensions. See [Live Sync configuration](configuration.md#live-sync)
for the endpoint, credentials and polling interval.

### Forum Topic groups

Open `/group` inside a Telegram Forum Topic. Each topic has its own session and
group state. The wizard selects characters, World Info and a turn mode:

| Mode | Behavior |
|---|---|
| Round-robin | Characters speak in a set order. |
| Contextual | The next speaker is selected from context. |
| Director | Uses the canonical Director's accepted speaker or viewpoint. If none is valid, uses conservative round robin without another provider call. |
| Manual | A user claims or passes the turn. |
| Autonomous | Characters continue within configured bounds. |

Manual turn ownership applies to text, message edits, photos, PNG documents and
voice input before processing. PNG card uploads also pass that check. Recovery
of an already-saved answer can finish even after ownership changes.

`/group goal <objective>` sets the same persistent objective shown in Director
Room, up to 4,000 characters. `/group goal status` reviews it and `/group goal clear`
clears it. Every change is recorded as a manual Director revision, not as story
dialogue. A forced group speaker remains an explicit choice; otherwise the group
uses the canonical plan or safe round robin. Configured cast members are not
automatically treated as physically present.

### Scene state

`/scene` shows structured location, weather, participants and continuity facts.
`/scene refresh` rebuilds that state with Utility; `/scene clear` removes it.
Scene state is optional, belongs to the session and leaves the transcript intact.
