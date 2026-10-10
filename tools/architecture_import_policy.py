"""Explicit dependency allowlists for low-level bridge owners."""

LOW_LEVEL_IMPORTS = {
    "bridge.diagnostic_operations": frozenset({"bridge.diagnostic_events"}),
    "bridge.npc_rollback": frozenset({"bridge.npc_repository", "bridge.repository_contracts"}),
    "bridge.token_usage_schema": frozenset(),
    "bridge.token_usage": frozenset(
        {"bridge.sqlite_store", "bridge.token_usage_repository", "bridge.token_usage_values"}
    ),
    "bridge.light_novel_contracts": frozenset(
        [
            "bridge.persona_service",
            "bridge.delivery_port",
            "bridge.job_service",
            "bridge.port_contracts",
            "bridge.provider_port",
            "bridge.session_service",
            "bridge.settings",
        ]
    ),
    "bridge.conversation_jobs": frozenset(["bridge.conversation_lifecycle"]),
    "bridge.conversation_lifecycle": frozenset(
        ["bridge.light_novel_repository", "bridge.meta_repository", "bridge.sqlite_store"]
    ),
    "bridge.conversation_schema": frozenset([]),
    "bridge.conversation_setup_state": frozenset(["bridge.metadata", "bridge.sqlite_store"]),
    "bridge.conversation_setup": frozenset(
        [
            "bridge.conversation_setup_state",
            "bridge.narrative_settings",
            "bridge.card_content",
            "bridge.conversation_lifecycle",
            "bridge.limits",
            "bridge.metadata",
            "bridge.persona_service",
            "bridge.session_repository",
            "bridge.session_titles",
            "bridge.settings",
            "bridge.sqlite_store",
        ]
    ),
    "bridge.conversation_setup_callbacks": frozenset(
        [
            "bridge.callback_tokens",
            "bridge.callbacks",
            "bridge.conversation_setup",
            "bridge.conversation_setup_panels",
            "bridge.conversation_setup_state",
            "bridge.metadata",
            "bridge.persona_service",
            "bridge.request_types",
            "bridge.telegram",
        ]
    ),
    "bridge.conversation_setup_panels": frozenset(
        [
            "bridge.conversation_setup_state",
            "bridge.narrative_panels",
            "bridge.narrative_settings",
            "bridge.callback_tokens",
            "bridge.card_content",
            "bridge.conversation_lifecycle",
            "bridge.panel_utils",
            "bridge.persona_service",
            "bridge.request_types",
            "bridge.session_repository",
            "bridge.telegram",
        ]
    ),
    "bridge.light_novel_callbacks": frozenset(
        [
            "bridge.closed_session_guard",
            "bridge.narrative_context",
            "bridge.narrative_values",
            "bridge.light_novel_contracts",
            "bridge.conversation_lifecycle",
            "bridge.job_service",
            "bridge.light_novel_format",
            "bridge.light_novel_jobs",
            "bridge.light_novel_repository",
            "bridge.light_novel_service",
            "bridge.metadata",
            "bridge.sqlite_store",
        ]
    ),
    "bridge.light_novel_commands": frozenset(
        [
            "bridge.conversation_lifecycle",
            "bridge.light_novel_panels",
            "bridge.metadata",
            "bridge.request_types",
            "bridge.telegram",
        ]
    ),
    "bridge.light_novel_format": frozenset(["bridge.telegram_output"]),
    "bridge.light_novel_jobs": frozenset(
        [
            "bridge.light_novel_contracts",
            "bridge.delivery_progress",
            "bridge.background",
            "bridge.card_content",
            "bridge.light_novel_panels",
            "bridge.light_novel_repository",
            "bridge.light_novel_service",
        ]
    ),
    "bridge.light_novel_panels": frozenset(
        [
            "bridge.narrative_context",
            "bridge.conversation_lifecycle",
            "bridge.light_novel_repository",
            "bridge.light_novel_service",
            "bridge.metadata",
            "bridge.request_types",
            "bridge.settings",
            "bridge.sqlite_store",
            "bridge.telegram",
        ]
    ),
    "bridge.light_novel_repository": frozenset(["bridge.repository_contracts"]),
    "bridge.light_novel_service": frozenset(
        [
            "bridge.context_compaction",
            "bridge.closed_session_guard",
            "bridge.narrative_context",
            "bridge.simulation_context",
            "bridge.card_content",
            "bridge.persona_service",
            "bridge.conversation_lifecycle",
            "bridge.job_store",
            "bridge.light_novel_format",
            "bridge.light_novel_repository",
            "bridge.model_selection",
            "bridge.provider_errors",
            "bridge.provider_port",
            "bridge.settings",
            "bridge.sqlite_store",
        ]
    ),
    "bridge.light_novel_turn": frozenset(
        [
            "bridge.narrative_context",
            "bridge.job_store",
            "bridge.light_novel_format",
            "bridge.light_novel_repository",
            "bridge.light_novel_service",
            "bridge.sqlite_store",
        ]
    ),
    "bridge.humanize": frozenset(["bridge.config", "bridge.provider_port"]),
    "bridge.humanizer_settings": frozenset(["bridge.metadata"]),
    "bridge.character_quality": frozenset(
        [
            "bridge.limits",
            "bridge.metadata",
            "bridge.model_selection",
            "bridge.provider_errors",
            "bridge.provider_port",
            "bridge.settings",
            "bridge.sqlite_store",
        ]
    ),
    "bridge.character_proposals": frozenset(
        ["bridge.limits", "bridge.meta_repository", "bridge.metadata", "bridge.request_types", "bridge.sqlite_store"]
    ),
    "bridge.character_optimizer_panels": frozenset(
        [
            "bridge.callback_tokens",
            "bridge.card_content",
            "bridge.cards",
            "bridge.character_quality",
            "bridge.panel_utils",
            "bridge.request_types",
        ]
    ),
    "bridge.character_optimizer": frozenset(
        [
            "bridge.card_content",
            "bridge.character_proposals",
            "bridge.character_quality",
            "bridge.provider_port",
            "bridge.request_types",
        ]
    ),
    "bridge.character_optimizer_input": frozenset(
        [
            "bridge.callbacks",
            "bridge.card_content",
            "bridge.character_optimizer",
            "bridge.character_optimizer_panels",
            "bridge.limits",
            "bridge.metadata",
            "bridge.provider_errors",
            "bridge.provider_port",
            "bridge.request_types",
            "bridge.telegram",
        ]
    ),
    "bridge.telegram_output": frozenset(),
    "bridge.rag_indexing": frozenset(
        (
            "bridge.rag_repository",
            "bridge.document_extraction",
            "bridge.embedding_port",
            "bridge.embedding_values",
            "bridge.limits",
            "bridge.settings",
            "bridge.sqlite_store",
        )
    ),
    "bridge.rag_query": frozenset(
        (
            "bridge.rag_repository",
            "bridge.embedding_port",
            "bridge.embedding_values",
            "bridge.metadata",
            "bridge.rag_retrieval",
            "bridge.settings",
            "bridge.sqlite_store",
        )
    ),
    "bridge.document_extraction": frozenset(("bridge.limits", "bridge.settings", "bridge.subprocess_security")),
    "bridge.database_backup": frozenset({"bridge.subprocess_security"}),
    "bridge.subprocess_security": frozenset(),
    "bridge.embedding_transport": frozenset(("bridge.network_security", "bridge.settings")),
    "bridge.embedding_values": frozenset(("bridge.rag_retrieval", "bridge.settings")),
    "bridge.rag_retrieval": frozenset(("bridge.rag_repository",)),
    "bridge.repository_contracts": frozenset(),
    "bridge.session_repository": frozenset({"bridge.repository_contracts"}),
    "bridge.topic_scope": frozenset(),
    "bridge.limits": frozenset(),
    "bridge.config": frozenset({"bridge.limits"}),
    "bridge.diagnostic_events": frozenset(),
    "bridge.diagnostic_logging": frozenset({"bridge.diagnostic_events"}),
    "bridge.diagnostic_workers": frozenset({"bridge.diagnostic_events"}),
    "bridge.diagnostic_reader": frozenset({"bridge.diagnostic_events"}),
    "bridge.background": frozenset({"bridge.limits", "bridge.diagnostic_events", "bridge.diagnostic_workers"}),
    "bridge.runtime_logging": frozenset({"bridge.settings", "bridge.diagnostic_events", "bridge.diagnostic_logging"}),
    "bridge.sqlite_store": frozenset({"bridge.limits", "bridge.settings", "bridge.schema", "bridge.scheduler_safety"}),
}
