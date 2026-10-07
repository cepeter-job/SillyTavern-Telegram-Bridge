"""Bounded Telegram text from the shared player-facing tracker projection."""

from datetime import datetime, timezone
from typing import Any

from bridge.simulation_values import text

_SECTIONS = (
    ("relationships", "Relationships"),
    ("agendas", "Agendas"),
    ("inventory", "Inventory"),
    ("skills", "Skills"),
    ("conditions", "Conditions"),
    ("factions", "Factions"),
    ("quests", "Quests"),
    ("tasks", "Tasks"),
    ("checks", "Recent d20 checks"),
)


def _status(value: str) -> str:
    return value.replace("native=", "Narrative: ").replace("_", " ")


def _line(section: str, item: dict[str, Any]) -> str:
    name = item.get("name", "")
    if section == "relationships":
        return f"{name}: BOND {item['bond']} ({item['tier']}) · Sparks {item['sparks']} · Grudge {item['grudge']}"
    if section == "agendas":
        return f"{name}: {item['objective']} [{item['step']}/{item['max_steps']}; {_status(item['status'])}]"
    if section in {"inventory", "skills", "conditions"}:
        return f"{name} ({item['modifier']:+d} {item['domain']})"
    if section == "factions":
        return f"{name}: {item['goal']} · Morale: {item['morale']} · Conflict: {item['conflict']}"
    if section == "tasks":
        return (
            f"{name}: {_status(item['status'])} [{item['progress_current']}/{item['progress_target']}] "
            f"{item['objective']} · {item['stage']}"
        )
    if section == "quests":
        progress = f" [{item['progress_current']}/{item['progress_target']}]" if item["progress_target"] else ""
        return f"{name}: {_status(item['status'])}{progress} · {item['objective']} · Reward: {item['reward']}"
    return (
        f"{item['domain']}: {item['action']} · d20 {item['roll']} {item['modifier']:+d} "
        f"= {item['roll'] + item['modifier']} vs DC {item['dc']} · {_status(item['outcome'])}"
    )


def format_tracker_view(data: dict[str, Any]) -> str:
    session = data.get("session")
    if session is None:
        return "Story trackers\n\nNo active session yet. Start a story or choose a session in Telegram."
    lines = ["📊 Story trackers — " + text(session.get("title") or "Current story", 120)]
    if data["last_updated_at"] is not None:
        updated = datetime.fromtimestamp(data["last_updated_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append("Last updated: " + updated)
    else:
        lines.append("No tracker update has been saved yet.")
    if data["pending"]:
        lines.append("Catching up with the latest story text. Showing saved state.")
    shortened = False
    has_items = False
    for section, title in _SECTIONS:
        items = data.get(section, [])
        if not items:
            continue
        has_items = True
        lines.append(f"\n{title} ({len(items)})")
        # Nine sections, two 160-byte lines each, plus bounded labels fit Telegram's UTF-16 limit.
        for item in items[:2]:
            full = _line(section, item)
            bounded = text(full, 160)
            if bounded != full:
                bounded += "…"
                shortened = True
            lines.append("• " + bounded)
        if len(items) > 2:
            lines.append(f"… {len(items) - 2} more.")
            shortened = True
    if not has_items:
        lines.append("\nNo visible tracker state yet.")
    if shortened:
        lines.append("\nSome details are shortened. Full view: Mini App → Manage → Story trackers.")
    lines.append("\nRead-only. Private plans and foreshadowing stay in Director Room.")
    return "\n".join(lines)
