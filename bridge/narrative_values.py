"""Immutable narrative values; no persistence, transport, or provider dependencies."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class NarrativeSettings:
    preset: str = "player_centric"
    pov_mode: str = "third_person_user"
    user_role: str = "protagonist"
    scene_focus: str = "user"
    offscreen_policy: str = "rare"
    user_control: str = "physical_continuity"
    director_cadence_mode: str = "adaptive"
    director_fixed_interval: int = 6
    ending_mode: str = "open_ended"
    require_finale_confirmation: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NarrativeState:
    active_scene_id: str = ""
    active_thread_id: str = ""
    story_phase: str = "setup"
    state_revision: int = 0
    history_revision: int = 0
    updated_through_rowid: int = 0
    viewpoint_character: str = ""
    pov_mode: str = ""
    user_present: bool | None = None


@dataclass(frozen=True, slots=True)
class NarrativeScene:
    scene_id: str
    thread_id: str
    viewpoint_character: str = ""
    pov_mode: str = "third_person_rotating"
    user_present: bool | None = None
    purpose: str = ""
    transition_type: str = "continue"
    start_rowid: int = 0
    end_rowid: int | None = None
    status: str = "active"
    source_revision: int = 0


@dataclass(frozen=True, slots=True)
class NarrativeThread:
    thread_id: str
    title: str
    status: str = "active"
    summary: str = ""
    last_scene_id: str = ""
    source_revision: int = 0


NARRATIVE_STEERING_PREFIX = "[Narrative steering]\n"
