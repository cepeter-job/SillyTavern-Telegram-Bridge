"""Built-in Help categories and command summaries."""

HELP_CATEGORIES = {
    "basic": [
        (
            "/start",
            (
                "Choose the opening greeting once after /new or /reset. "
                "Use /character for setup; ordinary dialogue requires /start first."
            ),
        ),
        ("/help", "Open this guide. Use /help <command> to jump straight to one command."),
        ("/cancel", "Cancel the current scoped input step without applying a change."),
        ("/status", "Show a formatted read-only session status message in Telegram."),
        ("/new", "Name and create a fresh isolated session, then switch to it."),
        (
            "/reset",
            (
                "Open a confirmation panel to clear only the active session and its "
                "Hindsight memory. Other sessions stay untouched."
            ),
        ),
        (
            "/session",
            (
                "Switch between sessions, create new ones, or delete inactive ones — "
                "deletion removes session data and its Hindsight documents."
            ),
        ),
        (
            "/sync",
            (
                "Open session-scoped Live API Sync controls; synchronization uses the "
                "local SillyTavern API only, not chat files or JSONL transfer."
            ),
        ),
        (
            "/update",
            (
                "Check the latest GitHub release. If you're already current, nothing "
                "happens. Otherwise a confirmation panel lets you update and restart."
            ),
        ),
    ],
    "characters": [
        (
            "/providers",
            (
                "Choose Story or Utility provider/model. Supported OpenAI-compatible, "
                "OpenAI Codex OAuth, Anthropic, and OpenCode transports can generate; "
                "catalog-only entries stay view-only. Open the provider list to use Provider health or Refresh models."
            ),
        ),
        (
            "/character",
            "Choose Character → Narrative Style → Normal/Light Novel → Persona → World → System Prompt → Session; "
            "Light Novel adds the A/B/C strategy step. Info, Optimizer, Upload and Delete remain available.",
        ),
        (
            "/lightnovel",
            "Open dedicated Light Novel mode controls or restore the current 3–4 full-text action choices with "
            "numbered selectors plus Next Scene. Mode changes require an unstarted standard session.",
        ),
        (
            "/director",
            "Inspect plans, steer scenes, configure Closed Story, recover its epilogue or create an Alternate Ending.",
        ),
        ("/narrative", "Choose this story's POV, cast focus, off-screen freedom, and user-control rules."),
        ("/persona", "Choose, create, edit, or disable a Persona. Delete only targets inactive, unreferenced ones."),
        ("/world", "Open World Info selection — activate or disable one or more lorebooks."),
        ("/note", "Open the Author's Note panel. Off clears it; User input waits for your next message."),
        ("/systemprompt", "Open the native JSON/TXT System Prompt picker. The prompt body stays private."),
        ("/language", "Open the reply-language panel for this session."),
        ("/language <language>", "Set the session reply language directly, or use auto to follow the user's language."),
        ("/expression", "Open native expression controls for the active character."),
        ("/imagine", "Open the image panel for Current Scene or Custom Prompt generation."),
    ],
    "generation": [
        (
            "/settings",
            (
                "Open this session's generation panel — temperature, tokens, sampling, stop sequences, "
                "Humanizer, and I am not MC mode. Reasoning is under /providers."
            ),
        ),
        (
            "/stream",
            (
                "Open the streaming on/off panel. Typed /stream on or /stream off still "
                "opens the panel; use its buttons to apply the change."
            ),
        ),
        ("/preset", "Open the preset panel — apply, save, or delete generation setting presets."),
        ("/macro", "Open scoped input for a SillyTavern macro preview."),
        ("/macro <text>", "Preview supported SillyTavern macros immediately against the supplied text."),
        ("/stscript", "Open the safe STscript panel for allowlisted bridge actions only."),
        ("/regen", "Generate a new response variant for the latest user turn."),
        ("/swipe", "Browse stored response variants and keep the one you like."),
        ("/branch", "Select existing response variants. For a new alternate ending of a closed story, open /director."),
        (
            "/check <domain> <DC> <action>",
            "Roll one recorded d20 check for an explicit action; retry never rerolls it.",
        ),
        ("/continue", "Continue the latest assistant response from where it stopped."),
        ("/edit", "Open scoped input for replacement text for the latest user turn."),
        ("/edit <text>", "Replace the latest user turn immediately and regenerate from the new text."),
        (
            "/retry",
            "Retry a failed reply, or recover a saved resolution/epilogue without regenerating committed prose.",
        ),
        ("/prompt", "Open the read-only prompt inspector with budget, memory/RAG, and group-context sections."),
        ("/prompt text", "Send the plain-text prompt diagnostic output without opening the inspector panel."),
    ],
    "memory_rag": [
        ("/memory", "Open Hindsight memory controls. Recall is always limited to the active session."),
        (
            "/memory search <query>",
            "Search Hindsight memory for the active session and show up to five matching remembered facts.",
        ),
        ("/memory curated", "Open the curated-memory panel to view durable distilled facts."),
        ("/memory curated refresh", "Refresh curated durable memory immediately with the configured Utility model."),
        (
            "/npc",
            (
                "Open the active session's persistent NPC Bank with visible dossiers, "
                "history, refresh and stale-safe undo."
            ),
        ),
        ("/remember", "Open scoped input for one explicit long-term fact."),
        ("/remember <fact>", "Store one explicit long-term fact immediately in active-session Hindsight memory."),
        ("/summarize", "Open a confirmation panel before regenerating the active-session summary."),
        ("/databank", "Open Data Bank RAG controls for status, listing, search, removal, and reindexing."),
        ("/databank search <query>", "Search active Data Bank document versions for matching chunks."),
        (
            "/databank versions <filename>",
            "List stored versions of one Data Bank filename and show which version is active.",
        ),
        ("/databank activate <filename> <version>", "Atomically activate a stored Data Bank version for retrieval."),
        ("/databank reindex [filename]", "Rebuild embeddings for all active documents, or only the named document."),
        (
            "/databank remove <filename> confirm",
            "Delete every stored version of one Data Bank filename after explicit confirmation.",
        ),
    ],
    "voice_group": [
        (
            "/voice",
            (
                "Open automatic quote-driven TTS controls. Typed on/off forms also open "
                "this panel rather than changing state directly."
            ),
        ),
        ("/voice_input", "Open transcription, STT model, and language controls."),
        ("/voice_input language", "Open the STT language panel — Auto, a fixed code, or User input."),
        ("/group", "Open Forum Topic group controls, including Director mode."),
        ("/group status", "Show current group state, mode, members, and speaker inside a Forum Topic."),
        ("/group add <character>", "Add a character to the current Forum Topic group, up to six members."),
        ("/group remove <character>", "Remove a character from the current Forum Topic group."),
        ("/group speak <character>", "Force one existing group member to be the next speaker."),
        ("/group mode <mode>", "Set round_robin, contextual, director, manual, or autonomous turn mode."),
        ("/group on|off", "Enable or disable the configured Forum Topic group directly."),
        ("/group next", "Advance the group speaker index to the next configured member."),
        ("/group goal", "View the hidden, session-local Director objective for this Forum Topic group."),
        (
            "/group goal <objective>",
            "Set or replace the hidden Director objective without adding it to the transcript.",
        ),
        ("/group goal clear", "Clear the hidden Director objective for the active Forum Topic group."),
        ("/scene", "Show the active session's structured scene-state panel."),
        ("/scene refresh", "Rebuild structured scene state immediately with the configured Utility model."),
        ("/scene clear", "Clear the active session's stored structured scene state without changing the transcript."),
    ],
}
