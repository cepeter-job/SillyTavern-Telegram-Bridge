"""Actor-name normalization for application-level memory visibility values.

SQL repositories still decode stored values without silently changing them.
"""

from __future__ import annotations


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
