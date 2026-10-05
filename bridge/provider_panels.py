"""Canonical provider panels owner."""

from __future__ import annotations

import logging
import time

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.config import REASONING_LEVELS
from bridge.generation_settings import get_generation_settings
from bridge.model_selection import director_reasoning_for_session, task_model_for_session, utility_reasoning_for_session
from bridge.panel_utils import panel_label, panel_page
from bridge.port_contracts import ProviderPolicy, ProviderProbes
from bridge.provider_discovery import get_model_groups, provider_health_checks
from bridge.provider_health_views import observation_age, provider_status_text
from bridge.provider_panel_tokens import ProviderReport, provider_action_token, store_provider_report


def send_model_menu(
    token: str,
    chat_id: str,
    current_model: str,
    provider_id: str | None = None,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context,
    provider_policy: ProviderPolicy | None = None,
    refresh_catalog: bool = True,
) -> None:
    groups = (
        get_model_groups(app_settings=request_context.app_settings)
        if refresh_catalog
        else get_model_groups(app_settings=request_context.app_settings, refresh=False)
    )
    if provider_id is None:
        options = [
            (group_id, f"{label} ({len(models)}){'' if is_supported else ' · catalog only'}", models, is_supported)
            for group_id, (label, models, is_supported) in groups.items()
        ]
        page_options, current_page, total_pages = panel_page(options, page)
        rows = []
        for group_id, label, models, _is_supported in page_options:
            mark = "✅ " if any(model_id == current_model for _, model_id in models) else ""
            rows.append(
                [
                    {
                        "text": mark + panel_label(label),
                        "callback_data": "provider:"
                        + dynamic_callback_token("provider", group_id, chat_id, db=request_context.db),
                    }
                ]
            )
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"models:providers:{current_page - 1}"})
            if current_page < total_pages - 1:
                navigation.append({"text": "Next ➡️", "callback_data": f"models:providers:{current_page + 1}"})
            rows.append(navigation)
        rows.append(
            [
                {"text": "🩺 Provider health", "callback_data": "provider:health"},
                {"text": "🔄 Refresh provider catalog", "callback_data": "provider:refresh"},
            ]
        )
        rows.append(
            [
                {"text": "⬅️ Back to target", "callback_data": "models:target"},
                {"text": "❌ Cancel", "callback_data": "models:cancel"},
            ]
        )
        text = f"Current model: {current_model}\nBridge provider catalog (page {current_page + 1}/{total_pages}):"
        if not options:
            text += (
                "\n\nNo provider models available. Configure models in the private YAML file selected "
                "by SILLYTAVERN_PROVIDER_CONFIG. Use Refresh provider catalog for enabled discovery."
            )
    else:
        label, models, is_supported = groups.get(provider_id, (provider_id, [], False))
        page_options, current_page, total_pages = panel_page(models, page)
        rows = []
        for model_label, model_id in page_options:
            mark = "✅ " if model_id == current_model else ""
            callback = (
                "model:" + dynamic_callback_token("model", model_id, chat_id, db=request_context.db)
                if is_supported
                else "unsupported:" + dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)
            )
            prefix = "" if is_supported else "🚫 "
            rows.append([{"text": prefix + mark + model_label, "callback_data": callback}])
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append(
                    {
                        "text": "⬅️ Previous",
                        "callback_data": (
                            "models:model:"
                            f"""{dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)}"""
                            ":"
                            f"""{current_page - 1}"""
                        ),
                    }
                )
            if current_page < total_pages - 1:
                navigation.append(
                    {
                        "text": "Next ➡️",
                        "callback_data": (
                            "models:model:"
                            f"""{dynamic_callback_token("provider", provider_id, chat_id, db=request_context.db)}"""
                            ":"
                            f"""{current_page + 1}"""
                        ),
                    }
                )
            rows.append(navigation)
        maintenance = []
        for action, title in (
            ("test", "Test provider"),
            ("refresh", "Refresh this provider"),
            ("reset", "Reset runtime"),
        ):
            if action == "reset" and provider_policy is None:
                continue
            handle = provider_action_token(action, provider_id, chat_id, request_context=request_context)
            maintenance.append({"text": title, "callback_data": f"provider:maint:{action}:{handle}"})
        rows.extend([[button] for button in maintenance])
        rows.append([{"text": "⬅️ Back to providers", "callback_data": "models:back"}])
        rows.append([{"text": "❌ Cancel", "callback_data": "models:cancel"}])
        text = f"Provider: {panel_label(label)}\nCurrent model: {current_model}"
        text += "\n\n" + provider_status_text(
            provider_id, app_settings=request_context.app_settings, provider_policy=provider_policy
        )
        if total_pages > 1:
            text += f"\nPage {current_page + 1}/{total_pages}"
        if not is_supported:
            text += "\nCatalog visible; this bridge adapter is not enabled yet."
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise


def _reasoning_label(budget: int) -> str:
    label = next((name.title() for name, value in REASONING_LEVELS.items() if value == budget), "Custom")
    return f"{label} ({budget})"


def send_model_target_menu(
    token: str, chat_id: str, current_model: str, utility_model: str, message_id: int | None = None, *, request_context
) -> None:
    if request_context.db is None:
        story_reasoning = utility_reasoning = director_reasoning = 0
        director_model = utility_model
    else:
        story_settings = get_generation_settings(request_context.db, chat_id, request_context.session_id)
        story_reasoning = int(story_settings.get("reasoning_budget") or 0)
        utility_reasoning = utility_reasoning_for_session(request_context.db, chat_id, request_context.session_id)
        director_reasoning = director_reasoning_for_session(request_context.db, chat_id, request_context.session_id)
        director_model = task_model_for_session(
            request_context.db,
            chat_id,
            {"session_id": request_context.session_id, "model_id": current_model},
            "director",
            app_settings=request_context.app_settings,
        )
    quote = (
        "Current models\n"
        f"📖 Story: {current_model}\n"
        f"🧠 Story reasoning: {_reasoning_label(story_reasoning)}\n"
        f"🛠 Utility: {utility_model}\n"
        f"🧠 Utility reasoning: {_reasoning_label(utility_reasoning)}\n"
        f"🎬 Director: {director_model}\n"
        f"🧠 Director reasoning: {_reasoning_label(director_reasoning)}"
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "📖 Story model", "callback_data": "modeltarget:story"},
                {"text": "🧠 Story reasoning", "callback_data": "models:story-reasoning"},
            ],
            [
                {"text": "🛠️ Utility model", "callback_data": "modeltarget:utility"},
                {"text": "🧠 Utility reasoning", "callback_data": "models:utility-reasoning"},
            ],
            [
                {"text": "🎬 Director model", "callback_data": "modeltarget:director"},
                {"text": "🧠 Director reasoning", "callback_data": "models:director-reasoning"},
            ],
            [{"text": "❌ Cancel", "callback_data": "models:cancel"}],
        ]
    }
    send_panel_message(
        token,
        chat_id,
        quote + "\n\nConfigure models:",
        markup,
        message_id,
        request_context=request_context,
        entities=[{"type": "blockquote", "offset": 0, "length": len(quote.encode("utf-16-le")) // 2}],
    )


def send_story_reasoning_menu(
    token: str,
    chat_id: str,
    message_id: int | None = None,
    *,
    request_context,
) -> None:
    settings = get_generation_settings(request_context.db, chat_id, request_context.session_id)
    current = int(str(settings.get("reasoning_budget") or 0))
    rows = [
        [
            {
                "text": ("✅ " if budget == current else "") + f"{label.title()} ({budget})",
                "callback_data": f"storyreasoning:{label}",
            }
        ]
        for label, budget in REASONING_LEVELS.items()
    ]
    rows.append([{"text": "✏️ Custom (0–32000)", "callback_data": "storyreasoning:custom"}])
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "models:target"},
            {"text": "❌ Cancel", "callback_data": "models:cancel"},
        ]
    )
    send_panel_message(
        token,
        chat_id,
        f"Story reasoning\nCurrent: {_reasoning_label(current)}\n\n"
        "Choose the reasoning budget used by Story-model replies.",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_utility_reasoning_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    _send_task_reasoning_menu(token, chat_id, message_id, request_context=request_context, task="utility")


def send_director_reasoning_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    _send_task_reasoning_menu(token, chat_id, message_id, request_context=request_context, task="director")


def _send_task_reasoning_menu(
    token: str,
    chat_id: str,
    message_id: int | None = None,
    *,
    request_context,
    task: str,
) -> None:
    getter = director_reasoning_for_session if task == "director" else utility_reasoning_for_session
    current = getter(request_context.db, chat_id, request_context.session_id)
    rows = [
        [
            {
                "text": ("✅ " if budget == current else "") + f"{label.title()} ({budget})",
                "callback_data": f"{task}reasoning:{label}",
            }
        ]
        for label, budget in REASONING_LEVELS.items()
    ]
    rows.append([{"text": "✏️ Custom (0–32000)", "callback_data": f"{task}reasoning:custom"}])
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "models:target"},
            {"text": "❌ Cancel", "callback_data": "models:cancel"},
        ]
    )
    send_panel_message(
        token,
        chat_id,
        f"{task.title()} reasoning\nCurrent: {_reasoning_label(current)}\n\n"
        f"Choose the reasoning budget used by {task.title()}-model tasks.",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_provider_health_menu(
    token: str,
    chat_id: str,
    message_id: int | None = None,
    *,
    request_context,
    provider_policy: ProviderPolicy | None = None,
    provider_probes: ProviderProbes | None = None,
    provider_id: str | None = None,
    report: ProviderReport | None = None,
    report_token: str = "",
    page: int = 0,
) -> None:
    """Reuse a bound report for paging; only an explicit new test performs network I/O."""
    if report is None:
        if provider_probes is not None:
            checks = provider_probes.check(provider_id)
        else:
            checks = (
                provider_health_checks(app_settings=request_context.app_settings)
                if provider_id is None
                else provider_health_checks(provider_id, app_settings=request_context.app_settings)
            )
        # Bound persisted reports and Telegram labels, even with an oversized private catalog.
        safe_checks = tuple((pid[:200], name[:80], status[:160]) for pid, name, status in checks[:1024])
        report = ProviderReport(safe_checks, time.time(), provider_id)
    if not report_token and request_context.db is not None:
        report_token = store_provider_report(report, chat_id, request_context=request_context)
    page_count = max(1, (len(report.checks) + 3) // 4)
    page = max(0, min(page, page_count - 1))
    selected = report.checks[page * 4 : (page + 1) * 4]
    blocks = []
    for pid, name, status in selected:
        details = provider_status_text(pid, app_settings=request_context.app_settings, provider_policy=provider_policy)
        probe_kind = "Local OAuth" if status.startswith(("authenticated", "not logged in")) else "Probe"
        blocks.append(f"{name}\n{details}\n{probe_kind}: {status}")
    text = (
        f"Provider health — page {page + 1}/{page_count}\n"
        f"Probe snapshot: {observation_age(report.checked_at)}\n"
        "Probes do not change runtime health. Inference probes can use quota.\n\n"
        + ("\n\n".join(blocks) if blocks else "No providers configured.")
    )
    rows = []
    if report_token:
        navigation = []
        if page > 0:
            navigation.append({"text": "Previous", "callback_data": f"provider:health-page:{report_token}:{page - 1}"})
        if page + 1 < page_count:
            navigation.append({"text": "Next", "callback_data": f"provider:health-page:{report_token}:{page + 1}"})
        if navigation:
            rows.append(navigation)
        rows.append([{"text": "Update runtime status", "callback_data": f"provider:health-page:{report_token}:{page}"}])
    if request_context.db is not None:
        for pid, name, _status in selected:
            handle = dynamic_callback_token("provider", pid, chat_id, db=request_context.db)
            rows.append([{"text": "Open " + panel_label(name), "callback_data": "provider:" + handle}])
    if report.provider_id is not None and request_context.db is not None:
        handle = provider_action_token("test", report.provider_id, chat_id, request_context=request_context)
        rows.append([{"text": "Test this provider again", "callback_data": f"provider:maint:test:{handle}"}])
    else:
        rows.append([{"text": "Run probes again", "callback_data": "provider:health"}])
    rows.append([{"text": "Refresh provider catalog", "callback_data": "provider:refresh"}])
    rows.append(
        [
            {"text": "Back to providers", "callback_data": "provider:back"},
            {"text": "Close", "callback_data": "models:cancel"},
        ]
    )
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise
