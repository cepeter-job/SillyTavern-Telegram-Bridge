# Native prompt assembly

The bridge builds its own requests. A native System Prompt is writing guidance,
not a SillyTavern Chat Completion preset export.

## Character-card post-history instructions

`post_history_instructions` on a character card are separate from the ignored
`post_history` field in a native System Prompt JSON file.

When the character card supplies post-history instructions, the native builder
uses this order:

1. Character definitions, selected session prompt, persona, contextual material,
   World Info, group speaker rules and the Author's Note.
2. Recent messages, or the character's opening greeting when history is empty.
3. The card's post-history instructions, followed by the bridge's narrative,
   user-agency, canonical state, Telegram formatting and language policies.
   When Light Novel mode is enabled, its response-envelope contract follows these
   late policies so character guidance cannot override the required structure.
4. The existing fixed-language reminder, when a response language is selected.
5. The current user request, including its retrieved context and optional image.

The card guidance appears once. Native policies take precedence over conflicting
card guidance and are not duplicated between the first and late system messages.
When the card has no post-history instructions, native policies stay in the first
system message and the existing message shape is unchanged.

Context compaction preserves fixed policies, post-history instructions and the
current user turn. It can remove older dialogue or optional retrieved material;
an oversized fixed prompt produces a budget error instead of silent truncation.

This order describes the native message builder. Adapters with a separate system
instruction field can consolidate system messages: native ordering is not a
claim of identical interleaving on every provider. No preset counters, module
activation, scoped variables or conditional macros are added by this change.

Use `/prompt` to inspect context budgeting and `/systemprompt` to select native
writing guidance. These controls do not alter a card's post-history field.
