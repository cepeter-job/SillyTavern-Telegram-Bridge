"""Actor-name normalization for application-level memory visibility values.

SQL repositories still decode stored values without silently changing them.
"""

from __future__ import annotations

from bridge.repository_contracts import NpcFieldState


def normalize_known_by(values: object) -> tuple[str, ...]:
    """Deduplicate case-insensitively while preserving the first display spelling."""
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = " ".join(str(value or "").split()).strip()
        folded = name.casefold()
        if not name or folded in seen:
            continue
        seen.add(folded)
        result.append(name)
    return tuple(result)


def npc_field_visible(state: NpcFieldState, active_character: str) -> bool:
    """Share NPC Bank's audience rule with other player-facing state readers."""
    if state.visibility != "restricted":
        return True
    active = normalize_known_by((active_character,))
    allowed = {name.casefold() for name in normalize_known_by(state.known_by)}
    return bool(active and active[0].casefold() in allowed)
