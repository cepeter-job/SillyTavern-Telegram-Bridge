# Native writing profiles

These optional profiles add prose guidance through the existing `/systemprompt`
menu. They are native JSON files, not imported SillyTavern Chat Completion
presets. No additional runtime, tracker, model call or persistent state is added.

| Menu label | File in `config/system_prompts.example/` | Intended use |
| --- | --- | --- |
| Native Scene Continuity | `writer_continuity.json` | Resolve attempts through established causes and stop at a natural handoff. |
| Native Grounded Dialogue | `writer_dialogue.json` | Distinct voices, purposeful dialogue and concrete narration without stock rhythm rules. |
| Native Ensemble Focus | `writer_ensemble.json` | Develop permitted cast interactions without changing native viewpoint or off-screen controls. |
| Native Magical Realism | `writer_magical_realism.json` | Practical presentation of the strange inside a compatible setting; never an automatic genre retcon. |

## Install and select

Copy only the desired example JSON file into the directory configured by
`SILLYTAVERN_SYSTEM_PROMPTS_DIR` (see [Configuration](configuration.md#paths-and-native-sillytavern-data)).
Do not replace an existing customized file: back it up or choose a different
`id`, `name` and filename for a separate variant. Examples are not automatically
installed or selected, and existing `natural`, `balanced` and `concise` examples
are unchanged.

Open `/systemprompt` in the intended session and select the corresponding menu
label. The native selector stores that selected text for the session. Editing
an example on disk does not retroactively rewrite stored session prompts;
reselect the profile to apply its new content. Select Off to remove only the
optional writing profile. Native narrative, agency, state, formatting and
language policies remain active.

One session System Prompt is selected at a time. Changing this selection replaces
that optional text, so preserve a custom prompt before switching. `/preset`
continues to save generation settings rather than modular writing instructions.

## Boundaries

Profiles cannot change the selected viewpoint, user-control permissions, speaker
ownership, off-screen policy or ending lifecycle. The native system owns those
settings. Profiles do not add dice, ledgers, tracker blocks, hidden turn counters,
HTML/JavaScript, choice formats or provider-specific sampling values. They use
no macros; full exports with variables and conditional modules need separate
interpretation and are not equivalent to these files.

Each new profile is limited to 2,400 characters by regression tests. This is an
authoring constraint, not a measured tokenizer or provider-usage claim. Prompt
assembly tests protect native policies, current-user content, off behavior and
fixed profile content during ordinary compaction. They cannot prove a model's
prose quality or semantic compliance.

## Design provenance

The four profiles are independently written native guidance, informed by the
2026-10-09 review of general techniques in the author-published sources below.
They are not copies, official ports, full compatibility layers or claims of
feature equivalence. The selected versions were inspected in sections, not
exhaustively evaluated as expanded prompt configurations.

- [Nemo Vivarium 1.0 Beta and Magpie v1 Beta-2.1](https://github.com/NemoVonNirgend/NemoEngine): attempted-action resolution, cast independence and separation of narrative lenses.
- [Pura's Director 16.0 and The Ethereality Express 1.1](https://github.com/platberlitz/platberlitz.github.io/tree/44f91c2e3d7ae4e0fd4c11618797422d1ecf11c5/preset): dialogue purpose, grounded narration, natural handoffs and practical magical realism.

No profile has been declared a quality winner or a production default. Real
provider comparisons require a frozen workload and blinded review; the roadmap
tracks that evidence separately from these offline checks.

See the [evaluation protocol](writing-profile-evaluation.md) for fixtures, blinding,
usage accounting and the explicit full-export compatibility boundary.
