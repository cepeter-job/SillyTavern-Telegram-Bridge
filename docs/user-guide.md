# User guide

## ✨ What it does

### 💬 Chat with your characters from Telegram

Send a message like you would to any contact. The bridge pulls together your
character card, Persona, World Info, System Prompt, conversation history, memory,
and any relevant documents — builds the prompt — and sends it to your model
provider. The reply comes back as a normal Telegram message.

If your provider supports streaming, you'll even see a live preview while it
generates. Pretty satisfying, honestly.

You can run multiple named sessions in the same chat. Each one keeps its own
transcript, model, settings, Persona, World Info, notes, variants, and group
state. Panel buttons are tied to the session that opened them, so an old menu
can't accidentally mess with a different session.

### 📂 Reads your native SillyTavern data

No duplicate catalogs. No sync conflicts. The bridge works directly with the
files SillyTavern already uses:

| What | Where |
|---|---|
| Character cards (PNG) | `data/default-user/characters/` |
| World Info / lorebooks | `data/default-user/worlds/` |
| System Prompts | `data/default-user/sysprompt/` |
| Persona names & descriptions | `settings.json` |
| Persona avatars | `data/default-user/User Avatars/` |
| Expression sprites | Tied to the active character |

Persona review and editing happen from `/persona`. The description appears in
Telegram's copyable code block above the edit buttons — handy when you're
tweaking a description on mobile.

When you swap characters or Personas, the bridge validates everything, checks
for protected targets, makes a backup, and confirms the change before reporting
success. **No silent overwrites. Ever.**

### 🔌 Providers and generation

A private provider catalog drives model selection. You can use
OpenAI-compatible Chat Completions, native OpenAI Codex OAuth, Anthropic
Messages, the keyless OpenCode Muse `/responses` transport, or an opt-in image
provider — all from the same panel. `/providers` starts with a block-quoted
snapshot of the active session's Story and Utility models and their reasoning
budgets. Both Story and Utility reasoning are configured from `/providers` and
remain independent per session. Story reasoning applies to Story-model replies;
Utility reasoning applies to Utility-model tasks such as summaries, curated
memory, scene state, Light Novel strategy B choices, and character
ranking/optimization.

Beyond basic generation, the bridge handles:

- 🎲 Response variants and branches
- ✏️ Editing your last message and regenerating
- 🔁 Retrying failed turns
- ➡️ Auto-continuation when output hits the token limit
- 🌐 Per-session reply language
- 🧠 Reasoning budgets
- 💾 Presets

Health checks, model discovery, endpoint validation, and streaming configuration
are all built in. Recognized provider rate limits, authentication/credit failures,
timeouts, unavailable models, upstream outages, and network failures are converted
to bounded actionable messages. Raw upstream response bodies, URLs, credentials,
and local paths are never shown in those user-facing errors.

### 🧠 Memory and retrieval

Hindsight gives each session its own memory. Recall is locked to the active
session — the bridge never pulls in broad user or character memory during
generation. You can also store explicit facts with `/remember` or rebuild
summaries with `/summarize`.

The Data Bank adds local full-text search and optional embeddings for PDF,
DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML files. Everything is
bounded — file sizes, page counts, extraction limits — so a large upload
can't run away with resources.

### 🎨 Media and groups

Send photos, documents, or voice messages and the bridge routes them where they
need to go — vision analysis, Data Bank ingestion, card validation, or
transcription. Automatic TTS can speak quoted dialogue from both your messages
and character replies. Expression sprites can be sent automatically.

And Telegram Forum Topics can host multi-character group sessions with
round-robin, contextual, manual, or autonomous turn modes.

---

## 🤖 Using the bot

The bot is built around panels. Send a command, get a menu, then tap buttons or
send the next message to complete the action. It keeps things predictable and
prevents accidental changes.

### 📋 Everyday commands

| Command | What it does |
|---|---|
| `/start` | Choose the opening greeting once after `/new` or `/reset` |
| `/help` | Open the interactive command guide |
| `/status` | Show formatted read-only session status |
| `/new` | Create and activate a named isolated session |
| `/reset` | Confirm an active-session reset and memory purge |
| `/session` | Switch, create, or delete inactive sessions |
| `/cancel` | Cancel the current scoped text-input step |
| `/character` | Manage cards and configure a Normal/Light Novel session |
| `/lightnovel` | Dedicated A/B/C mode controls and current-choice recovery |
| `/persona` | Choose, create, edit, or disable a native Persona |
| `/world` | Choose or disable World Info/lorebooks |
| `/systemprompt` | Choose a native JSON/TXT SillyTavern System Prompt |
| `/note` | Configure the session Author's Note |
| `/providers` | Choose Story or Utility provider/model |
| `/update` | Check for a release update and confirm before applying it |

### 🔄 Replies and generation

| Command | What it does |
|---|---|
| `/settings` | Open sampling, token, stop-sequence, Humanizer, and Grounded User controls |
| `/stream` | Open streaming preview controls |
| `/preset` | Apply, save, or delete generation presets |
| `/prompt` | Open the read-only prompt inspector |
| `/regen` | Generate another response variant |
| `/swipe` | Browse stored response variants |
| `/branch` | Choose the active response branch |
| `/continue` | Continue the latest assistant response |
| `/edit` | Edit the latest user turn and regenerate |
| `/retry` | Recover a failed response or incomplete saved delivery |
| `/language` | Choose the model reply language |
| `/expression` | Choose native expression behavior |
| `/macro` | Preview supported SillyTavern macros |
| `/stscript` | Open allowlisted STscript actions |

### 🎙️ Voice, files, memory, and groups

| Command | What it does |
|---|---|
| `/voice` | Open automatic quote-driven TTS controls |
| `/voice_input` | Configure transcription, STT model, and language |
| `/imagine` | Generate an image through an enabled image provider |
| `/memory` | Open active-session Hindsight memory controls |
| `/remember` | Store one explicit long-term fact |
| `/summarize` | Confirm active-session summary regeneration |
| `/databank` | Open Data Bank RAG controls |
| `/sync` | Open Live API Sync controls |
| `/group` | Open Forum Topic group controls |
| `/scene` | Open structured scene-state controls |

The README intentionally keeps this list to top-level commands. `/help` is the
canonical command reference for direct typed forms and subcommands. For example,
`/help databank search`, `/help group mode`, `/help group goal`, and
`/help scene refresh` open the matching detailed entry.

> `/tts` is not a command. Automatic voice is controlled from `/voice`.


### How to format your messages

When you send a message, you can use a couple of markers to tell the bridge
what's action and what's dialogue:

```text
*She walks toward the doorway.*    → Action/narration
"I heard something outside."        → Dialogue (also queued for TTS)
I heard something outside.          → Plain dialogue, no markers
**bold text**                       → Literal text, not an action marker
```

Here's how it works:

- `*text*` (single stars) → sent to the model as an action. The stored
  transcript stays unchanged.
- `"text"` (straight double quotes) → treated as dialogue and queued for TTS
  when automatic voice is enabled from `/voice`. Works for both your messages
  and character replies.
- `**text**` (double stars) → preserved literally. Not an action, not spoken.
- Curly or "smart" quotes → not recognized as TTS delimiters. Use straight
  quotes.

### Panels and cancellation

Some commands need you to type something — a session name, a memory fact, an
image prompt. When that happens, the bot opens a scoped input step and waits
for your next message.

Send `/cancel` to back out. Invalid input keeps the prompt open with feedback.
Valid input applies the change and returns you to the panel. Pending inputs
expire after a while, and stale callbacks are rejected rather than applied to
the wrong session.

Dynamic panel choices use random, chat-scoped handles stored in SQLite. They
expire after 15 minutes, survive service restarts while valid, and are rejected
when their chat or panel ownership does not match. A failed database write does
not issue a memory-only handle. Expired handles are pruned when new handles are
created; token resolution itself never commits or mutates a request transaction.

`/stscript` only exposes allowlisted bridge actions. It can't run shell
commands, touch the filesystem, or make network requests. Its Reset action goes
through the normal confirmation flow.

---

## 🗂️ Sessions and memory

### Session lifecycle

`/new` asks for a name (1–80 characters), creates a fresh session, and switches
to it. Starting the flow closes your previous unclosed management panel in the
same chat/topic, but does **not** delete the previous session's Telegram messages
or stored conversation. `/session` lists all sessions and lets you switch, create,
or delete inactive ones. Both the custom name and the internal session ID show up
in `/status`.

New standard sessions are **unstarted**. Use `/character` to configure the story,
then `/start` to choose the character's Default or Alternate opening message.
Before that opening is committed, dialogue and conversational media return
`Please use /start command.` without a story-model call or transcript entry.
Setup commands and scoped management input remain usable. After starting,
`/start` returns `This session has already started.`; the plain word `start` is
no longer a command alias. `/start` does not launch setup or probe the model.

The conversation-state migration marks existing non-empty sessions as started
once; empty sessions require `/start`. Runtime decisions use the explicit state,
not the current number of messages. Group-session orchestration is unchanged.

Each session carries its own:

```text
Conversation transcript
Selected Story/Utility models and reasoning settings
Response language
Persona and World Info
Author's Note and System Prompt
Response variants and branch state
Summary and failed-turn state
Forum Topic group state, when applicable
```

### Reset and deletion

`/reset` clears the active session **without deleting it**:

1. Opens a confirmation panel — nothing is touched yet.
2. On confirm, purges Hindsight documents for that session only.
3. Best-effort deletes the current session's tracked Telegram user inputs,
   assistant replies, and Light Novel choice/selection messages.
4. Clears the local conversation, variants, failed turns, summary, and data.
5. The session stays available, now empty.
6. Preserves Character, Normal/Light Novel mode, A/B/C strategy, Persona, World
   and System Prompt, but invalidates old choices and returns the standard session
   to unstarted. Use `/start` to choose the opening greeting again.

The confirmation text spells out exactly what gets deleted. Telegram deletion is
best-effort because Telegram can reject old messages or group deletions without
sufficient permissions; such failures do not roll back the durable reset. Cleanup
is scoped to the active session and chat/topic, so other sessions/topics are not
touched.

`/session` deletion is stricter. It only targets inactive sessions, refuses
sessions with running or queued jobs, and requires Hindsight cleanup to succeed
before removing local data. If cleanup can't be verified, the session is kept.
Other sessions and their memories are never touched.

### Light Novel mode

`/character` is the setup entrypoint for standard sessions:

```text
Character → Normal / Light Novel
                       └─ A / B / C (Light Novel only)
          → Persona → World → System Prompt → Session → Apply
```

Normal skips the A/B/C step. Persona, World and System Prompt offer Off/Skip;
World permits multiple lorebooks. The final step selects an unstarted session
or creates a named session. Selections remain an actor-scoped draft until Apply
validates the references and commits all configuration together. An already
started session must be reset first or replaced with a new session. Applying
setup does not start the story: run `/start` afterward.

`/lightnovel` is the dedicated mode/status command; there is no Light Novel entry
in `/settings`. It offers A, B, C or Normal while the session is unstarted. During
a story it restores the current choice/retry panel without regenerating the story.

| Strategy | Story generation | Choices |
|---|---|---|
| A — Story Inline | Story and choices in one structured response | Extracted before visible prose is rendered |
| B — Utility Model | Normal Story-model request | A separate Utility-model request |
| C — Story Second Pass | Normal Story-model request | A second request to the Story model |

After `/start` commits the card-authored greeting, every Light Novel strategy
schedules its first choice-only request immediately: A/C use the Story model;
B uses the configured Utility route. Subsequent successful A turns need no
additional choice-generation request. If A returns usable narrative but its inline
choices are missing or invalid, the story remains committed and the existing
durable choice worker automatically performs one choice-only Story-model repair
pass. While that repair is pending, the panel says that choices are being
prepared; **Retry Choices** appears only if automatic recovery finishes without
valid choices. A's raw structured response is not streamed to Telegram. B/C
retain ordinary story streaming when other settings allow it. Response language
and Humanizer still apply to visible narrative; choices are not passed through
Humanizer.

Choice generation remains grounded in the configured session, but each strategy
uses that context differently:

- **A — Story Inline** generates the narrative and choices together in one
  Story-model request. The choice contract is added to the same assembled prompt
  that is sent for the narrative after normal context compaction. Choices therefore
  see the Character, Persona, selected System Prompt, active World Info,
  conversation history, Author's Note, post-history instructions,
  response-language rules, and any session summary, memory, or RAG context that
  remains in the final prompt. When the prompt is over budget, optional context may
  be trimmed by the same compaction rules used for ordinary narrative generation.
- **B — Utility Model** and **C — Story Second Pass** generate choices after the
  narrative is committed. Both receive the same bounded snapshot: Persona name
  and description, Character name/description/personality/scenario, relevant
  World Info, the selected session System Prompt, the six most recent messages,
  and the current story. B sends that snapshot to the Utility model; C sends it
  to the Story model.
- The B/C snapshot limits each Persona field to 4,000 characters, each Character
  field to 2,000, World Info to 6,000, the System Prompt to 4,000, each recent
  message to 1,600, and the current story to its final 10,000 characters. It does
  not separately resend Author's Note, post-history instructions, memory, RAG, or
  the session summary; those can still influence choices indirectly through the
  already-generated current story.

Every strategy asks the model to propose actions for the **user**, not actions
for the assistant Character. The dedicated B/C choice prompt additionally treats
the supplied snapshot as story data that cannot override the bounded output
contract.

Each new story turn requests a uniformly random **2, 3 or 4** distinct actions.
The count is reserved before generation; retries/restarts do not reroll it, and
ready choices are reused unchanged. The panel message shows every generated
action in full with a numbered label; compact `1`–`4` selector buttons sit below
it, followed by a permanent **⏭ Next Scene** row. Tapping a numbered choice submits
the exact stored action as the next user turn. Next Scene instead submits a fixed
narrative instruction to advance without speaking, deciding, or acting for the
user character until that character can meaningfully participate again. Both use
the normal history, memory, RAG, provider and durable-worker pipeline. After a
button is consumed, Telegram replaces the panel with a bot-owned block quote of
the selected action; the internal Next Scene instruction is never shown.

You may type your own reply after `/start`. That reply or conversational media
invalidates old choices before admission; stale panels cannot create another
branch. Choices are bound to the originating user, chat, session, reset epoch,
assistant revision and panel. Consumption and durable enqueue share a transaction,
so double taps or process recovery cannot create two committed user turns.

When automatic choice generation finally fails, the committed story remains
available with **Retry Choices**. Retrying repairs choices only; A uses the same
Story-model repair path, B the Utility route, and C the Story model. Missing panel
delivery can be restored with `/lightnovel`. Telegram cleanup is best effort;
invalidation in SQLite remains authoritative even if an old button is still
visible.

Normal mode generates no Light Novel choices or additional choice-model calls.
`/swipe` remains a selector for alternate assistant responses, not user actions.
Light Novel mode currently applies only to standard sessions; group orchestration remains unchanged.

### Memory boundaries

All automatic Hindsight recall and `/memory search` are scoped to the active
`session:<session_id>` tag. `/remember` stores one fact at a time through a
scoped prompt. `/summarize` rebuilds the active session's summary from its
stored transcript.

Memory and retrieved documents are treated as untrusted context — bounded and
sanitized before they reach the model.

---

## 📝 Generation and delivery

### What goes into a prompt

When you send a message, the bridge assembles the prompt from:

```text
Character card fields and example dialogue
Native Persona description
Active World Info entries
Selected System Prompt
Session Author's Note
Conversation history and summary
Active-session Hindsight recall
Data Bank references
Response-language instruction
Provider-specific generation settings
```

Your original user text is stored as entered. Single-star action formatting
is added only to its prompt representation. Assistant replies are stored after
any selected response-language rendering and optional Humanizer pass.

### Streaming and long responses

With streaming on, the bot posts a temporary preview that updates as the model
generates. Once the full response is ready, the preview is removed and the
final message is sent through the normal splitter.

Long messages are split at Telegram's UTF-16 limit. The splitter looks for
natural break points in this order:

1. Paragraph boundaries
2. Newlines
3. Sentence boundaries
4. Whitespace
5. A hard UTF-16-safe cut

When a provider stops at the token limit (`finish_reason: length`), the
bridge tries bounded auto-continuation. Streaming continuation stays streaming,
keeps the preview cumulative across segments, honors cancellation between and
during continuation requests, and makes at most three automatic continuation
requests after the initial visible segment. Reasoning-only length stops can
retry with a larger output budget. `/continue` is always available for a
deliberate additional segment.

### Optional Humanizer response style

Open `/settings` and tap the single **Humanizer: ON/OFF** button to toggle an
additional prose rewrite after response-language rendering. It is **off by default**, scoped to
the selected session, and reset by **Reset all** in the generation settings
panel. It uses the response's selected provider/model, so enabling it can add
latency and token charges. Native transcript sync preserves the setting.

With Humanizer enabled, normal replies do not expose an intermediate raw
streaming preview. The rewrite receives at most 24,000 source characters, has
an output-token request capped at 4,096, and uses a 30-second **per-request**
provider timeout. Existing bounded provider recovery/continuation may involve
additional requests; this is not a 30-second whole-turn deadline. Longer source
texts bypass the rewrite rather than sending a truncated source.

Provider failure, an empty rewrite, excessive shortening, or changes to protected
code, numbers, quoted dialogue, action spans, links, or citations keep the
original rendered reply. These conservative structural checks are not a proof
of semantic equivalence: review important prose as with any model-generated text.
The setting applies to normal replies, regeneration, continuation, edited-message
regeneration, and image replies through their shared rendering paths.

No weekly Humanizer reference sync, timer, or automatic prompt promotion is installed. The active prompt changes only through reviewed source changes and the normal release process. Prompt attribution is retained in [third-party notices](../THIRD_PARTY_NOTICES.md).

### Optional Grounded User mode

Open `/settings` and toggle **Grounded User: ON/OFF**. It is **off by default**
and scoped to the selected session. Unlike Humanizer, it does not make a second
provider request: it adds a compact policy to the Story prompt.

When enabled, explicit Persona/story advantages remain valid, but the model is
asked not to invent extra competence, authority, knowledge, admiration,
attraction, protection, or plot importance merely because the user is the
protagonist. NPCs keep independent goals, loyalties and preferences, and
success/failure/consequences should follow established abilities, preparation,
circumstances and prior events. The policy also explicitly rejects the opposite
failure mode: it must not punish, humiliate, weaken, or force failure simply to
be "anti-player."

Normal replies, regeneration, edits, continuation, image-context replies and
Light Novel inline narrative share the main grounded Story prompt. Separate
Light Novel choice generation additionally avoids choices that presume success
or unearned authority, and Group Director speaker selection avoids choosing an
NPC merely to make the user the center of attention.

### Telegram-safe model output

Completed model replies are normalized for Telegram before they are stored and
delivered. Presentation HTML such as `<div>`, `<span>`, headings, lists and
`<br>` is converted to readable plain text; HTML entities are decoded. HTTP(S)
HTML links retain their destination, and Markdown URL/email autolinks remain
unchanged. Fenced and inline code are protected so literal HTML examples remain
copyable. This is
a final-output compatibility step rather than Telegram `parse_mode=HTML`, whose
limited tag set cannot safely render arbitrary model-generated web markup.

### Variants and recovery

| Command | Action |
|---|---|
| `/regen` | New response variant |
| `/swipe` | Browse and pick from stored variants |
| `/branch` | Switch the active response branch |
| `/edit` | Replace your last message and regenerate |
| `/retry` | Recover a failed response or incomplete saved delivery |

Queued `/edit`, `/regen` and `/continue` commands keep the session selected when
they were queued. Switching the active session does not redirect their work or
their restart recovery.

When an answer has been saved but Telegram delivery fails, durable jobs allow
three total delivery attempts, including interrupted attempts across restarts.
Recovery uses the saved answer and skips chunks
whose Telegram acknowledgements were recorded; it does not generate another
answer. If automatic attempts are exhausted, the original actor can use `/retry`
in the original session. A deleted or replaced answer cannot be recovered.

A saved greeting can resume delivery even after its selection panel expires or
you switch the active session. Light Novel choices leave pending greeting
delivery with its original job and retry limit.

Telegram delivery and the local database are separate systems. A crash after
Telegram accepts a chunk but before its acknowledgement is saved can still
leave delivery uncertain. Recorded acknowledgements prevent those known chunks
from being resent; the bridge cannot promise exactly-once delivery across an
unrecorded external response.

---

## 🎭 Native SillyTavern data

### Characters

`/character` lets you pick from native PNG cards, view metadata, get upload
guidance, and delete cards through a protected flow. Character Info displays the
selected card PNG directly in Telegram with its metadata summary and panel
controls; if Telegram cannot render the PNG, the bridge falls back to the text-only
info view. Uploaded cards are validated as real SillyTavern PNGs. Backups are made
before any replacement or deletion.

The active card and any cards referenced by sessions or groups are protected —
you can't accidentally delete a card that's in use.

Ranked character buttons in the main Character Menu, Character Info picker and
Optimizer picker reuse Telegram's registered custom-emoji icons from
`sttb_ranks_by_SillyTavernPunzmeBot`; the current session character keeps the
main picker's `✅` prefix, while unranked characters keep a plain name button.
The selected Character Info and Optimizer details also show an explicit
`Rank: S/A/B/C/D` label, or `Rank: —` when no valid cached rank exists. The
checked-in [rank asset manifest](../assets/character-ranks/README.md) contains
public GIF/WEBM references, SHA-256 provenance, and the exact hardcoded mapping.

#### Re-uploading and optimizing a card

A first upload installs a validated card with a verified backup. Re-uploading
an existing name opens **Overwrite / New version / Keep existing** instead of
silently replacing it. The pending file stays outside the visible character
catalog. Confirmations belong to the initiating user and session, expire after
10 minutes, and are single-use. A newer proposal supersedes that user's prior
proposal of the same type. Changes to the installed card after preview require
a new preview rather than overwriting the changed file.

The **Optimizer** entry in `/character` first opens **Auto Optimize** and
**Manual Suggestion**. Auto uses the configured utility-model route directly.
Manual Suggestion first shows the current editable card values in readable
Telegram sections and ends with an explicit **Please input your revision prompt now.** instruction. The first preview uses **Revise** instead of Manual Suggestion; each Revise action reopens the prompt against the current temporary values. Prompts are limited to 2,000 characters, for example “make her more sarcastic, preserve the backstory, and shorten the first message”. The guidance goes to the same utility model without overriding the
optimizer field whitelist or character-identity rules. Suggestions are bound to
the initiating user, session, character file and original file digest, expire
after 10 minutes, and are not written into the installed card until Apply. Users
in the same Telegram chat keep independent pending Manual Suggestions. Choosing
**Revise** from a preview is a true revision: the displayed base and
the next LLM request use that temporary preview's values, while the final staged
proposal keeps the cumulative effective diff relative to the unchanged installed
card. This preserves the existing checksum and exact-byte Apply verification.

The Optimizer is a model-assisted editing tool, not a character-quality
guarantee. Review all pages of the proposed fields before applying; the preview
includes system prompt and post-history instructions when changed. Application
verifies that the exact approved fields produce the staged card bytes, preserves
name/avatar/other metadata, backs up the original bytes, and atomically replaces
the file. Consistent duplicate `chara` chunks and paired v2 `chara`/v3 `ccv3`
metadata are updated together while preserving each schema's unrelated fields;
conflicting or malformed embedded copies are refused rather than partially
rewritten. Character display remains compatible with the canonical `chara`
payload, while every Optimizer write validates all `chara` and `ccv3` copies
before staging a preview. A failed or interrupted application may require generating
a new preview; it never replays an already consumed confirmation automatically.
After a successful optimizer Apply, the installed card is reranked immediately
with the Utility model. If reranking is unavailable, the changed file revision
invalidates the previous cached rank instead of showing a stale grade.

New installations and applied card changes also request an optional S–D quality
tier from the utility-model route. Badges are model-generated assessments, not
objective scores. Unavailable ranking leaves the card usable without a new badge.
Stored badges are invalidated when the card's file revision changes. Ranking
and optimization do not enter the roleplay transcript, but they send card text
to the selected utility provider and can incur token charges. Utility tasks use
bounded inputs and per-request timeouts.

### Personas

`/persona` reads and writes native SillyTavern Persona settings and avatar
storage. There's no second Persona catalog — the bridge uses what SillyTavern
already has. The active Persona and any Personas referenced by other sessions
are protected from the inactive-delete picker.

If a session has no valid Persona selected, the bridge tries to resolve
SillyTavern's native default. If there's no explicit default but exactly one
Persona exists, that one is used. If nothing safe can be found, the bridge
falls back to a generic label rather than exposing a private identity.

### World Info and System Prompts

`/world` selects or disables one or more native World Info JSON files. Active
lorebooks are path-validated and merged deterministically at prompt time. The
panel also supports uploading a `.json` World Info document. Uploads must use
SillyTavern's `{ "entries": { ... } }` format, are limited to 10 MB, and refuse
to overwrite an existing filename. The trash button deletes only inactive
World Info files; files referenced by any session are protected.

`/systemprompt` reads native System Prompts from:

```text
$SILLYTAVERN_DIR/data/default-user/sysprompt/
```

JSON files use `name` and `content` fields. TXT files in that directory are
also supported. The directory is the sole System Prompt source; the bridge no
longer supports a separate single-file prompt fallback. The bridge currently
ignores native `post_history` fields. Prompt bodies stay private — menus and
`/status` only show labels or status.

### Expressions

`/expression` supports automatic classification, manual sprite selection, and
off mode. Sprites are sent only when the effective expression actually changes.
If a matching sprite isn't available, the bridge falls back to a neutral
sprite, then the character avatar, and finally text-only.

---

## 🎙️ Voice, images, and documents

### Automatic TTS

Open `/voice` and enable automatic voice; the bridge will speak dialogue wrapped
in straight double quotes — from both your messages and character replies:

```text
You send:     "Please wait for me."
Character:    *turns to look* "I will wait."
```

Both quoted lines get queued for TTS. Actions, narration, and unquoted text
stay text-only. Ordinary text messages also disable Telegram link previews, so
a character card URL can't turn into a footer image. The transcript is always
stored as plain text, and TTS jobs run in the utility queue with idempotent
operation IDs so retries never duplicate audio.

### Voice input

`/voice_input` sets up transcription for Telegram voice messages using
Faster-Whisper. Pick a STT model and choose:

```text
Auto
A fixed 2–8 letter language code
Scoped User input
```

Transcription runs as a durable background job, then enters the session as a
normal text turn.

### Images

`/imagine` stays disabled until you explicitly configure an image provider with
an endpoint, model, and output size. Prompts must be 1–4,000 characters.
Chat-only models are never silently reused for image generation.

Telegram photos with captions are queued for vision analysis when the active
model supports vision. If it doesn't, the bridge fails closed — no changes to
the transcript.

### Documents and Data Bank

Telegram Documents are routed to either character-card validation or Data Bank
ingestion. The Data Bank accepts PDF, DOCX, TXT, Markdown, JSON, YAML, CSV,
HTML, and XML. File size, PDF page count, DOCX expansion, extracted text
length, and embedding work are all bounded.

---

## 🔄 Live Sync and Forum Topic groups

### Live Sync

Live Sync is off by default. It uses SillyTavern's loopback API and processes
the API's chat-record response directly. The bridge performs an initial
reconciliation before turning on realtime updates. If anything looks wrong —
auth errors, schema mismatches, sync-ID mismatches, two-sided conflicts, or
oversized records — **sync stops rather than silently picking a side**.

```dotenv
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

Live API Sync is the only conversation synchronization path. The bridge does
not install extensions, poll chat files, import/export JSONL transcripts, or
expose Live Sync credentials in Telegram.

### Forum Topic groups

In manual mode, native Telegram message edits, photos, image documents and voice
messages obey the same user-turn rule as new text. The rule is checked before
enqueue and again before downloading, transcription or generation, including
recovered jobs. An edit targets the session containing its original message.
Delivery of an already saved answer can recover after the turn owner changes.

A PNG document might be a character card or a conversation image, so it must
pass the turn check before downloading, including uploads reported as generic
binary files. This also restricts out-of-turn PNG character-card uploads. JSON
management uploads remain available under their existing policies.

`/group` only works inside a Telegram Forum Topic. Each topic gets its own
isolated session and group state. The setup wizard lets you create a group
session, pick characters and World Info, and choose a turn mode:

| Mode | How it works |
|---|---|
| **Round-robin** | Characters speak in a set order |
| **Contextual** | The bridge picks the next speaker based on context |
| **Manual** | An owner claims or passes the turn; ownership is verified server-side |
| **Autonomous** | Characters continue on their own within configured bounds |

Group state changes and generated turns are durable. Topic IDs are kept
internal for isolation and only attached to Telegram payloads when sending.

---

## 🎬 Director goals and scene state

Director mode can keep a hidden, session-local objective for a Forum Topic group:

```text
/group goal <objective>   Set or replace the objective
/group goal               Show the current objective
/group goal status        Show the current objective
/group goal clear         Remove it
```

The objective helps the invisible Director choose the next speaker and guide the
scene without entering the roleplay transcript or being revealed to the
characters. The operator can inspect it with `/group goal status`. It is bounded
to 1,200 characters and applies only while the session is in Director mode.

The bridge also maintains structured scene state — location, weather, participants,
known facts, and other bounded continuity details — outside the transcript:

```text
/scene          Show the current structured state
/scene refresh  Rebuild it with the configured utility model
/scene clear    Remove it
```

Scene refresh is a background utility-model task. It is optional, session-scoped,
and never replaces the original conversation history.

---
