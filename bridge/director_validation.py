"""Pure Director validation: proposals are guidance, never committed story facts."""

from __future__ import annotations

import json

from bridge.director_contracts import DirectorProposal, DirectorProposalError, parse_director_proposal
from bridge.narrative_policy import NarrativePolicy
from bridge.narrative_values import NarrativeState


def validate_director_proposal(
    proposal: DirectorProposal,
    *,
    policy: NarrativePolicy,
    state: NarrativeState,
    valid_characters: set[str],
    ending_state: str = "open",
    valid_threads: set[str] | None = None,
    user_characters: set[str] | None = None,
) -> None:
    # UI-authored values and future callers use the same bounded contract as model output.
    try:
        proposal = parse_director_proposal(json.dumps(proposal.to_dict(), ensure_ascii=False))
    except TypeError as exc:
        raise DirectorProposalError("parse", "Director proposal contains invalid field types") from exc
    if ending_state in {"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"}:
        raise DirectorProposalError("lifecycle", "Ordinary Director planning is unavailable during story closure")
    if proposal.expected_revision != state.state_revision:
        raise DirectorProposalError("stale", "Director proposal used an outdated story revision")
    users = {name.casefold() for name in (user_characters or set())} | {"user", "{{user}}"}
    cast = {name.casefold() for name in valid_characters}
    speaker = proposal.speaker.casefold()
    viewpoint = proposal.viewpoint.casefold()
    if speaker in users:
        raise DirectorProposalError("policy", "The user cannot be selected as an autonomous speaker")
    if speaker and speaker not in cast:
        raise DirectorProposalError("reference", "Director speaker is not an established cast member")
    if proposal.pov and proposal.pov != policy.settings.pov_mode:
        raise DirectorProposalError("policy", "Director viewpoint mode conflicts with Narrative Style")
    if viewpoint in users:
        if proposal.pov == "first_person" or policy.settings.pov_mode == "first_person":
            raise DirectorProposalError("policy", "First-person narration cannot take over the user")
        if proposal.user_present is False:
            raise DirectorProposalError("policy", "An absent user cannot be the viewpoint character")
    elif viewpoint and viewpoint not in cast:
        raise DirectorProposalError("reference", "Director viewpoint is not an established character")
    if proposal.scene_id and proposal.scene_id != state.active_scene_id:
        raise DirectorProposalError("reference", "Director scene reference is not the current scene")
    threads = valid_threads if valid_threads is not None else {state.active_thread_id}
    if proposal.action == "continue":
        if proposal.new_thread is not None or proposal.transition_type != "continue":
            raise DirectorProposalError("policy", "Continuing a scene cannot create a new thread or transition")
        if proposal.thread_id and proposal.thread_id != state.active_thread_id:
            raise DirectorProposalError("reference", "Continuing a scene cannot switch threads")
        if viewpoint and viewpoint != state.viewpoint_character.casefold():
            raise DirectorProposalError("policy", "A viewpoint change requires a scene transition")
        if proposal.pov and state.pov_mode and proposal.pov != state.pov_mode:
            raise DirectorProposalError("policy", "A viewpoint-mode change requires a scene transition")
        if proposal.user_present is not None and state.user_present is not None:
            if proposal.user_present != state.user_present:
                raise DirectorProposalError("policy", "Continuing a scene cannot invent user arrival or departure")
        return
    if proposal.action != "transition_scene":
        raise DirectorProposalError("parse", "Director action is unsupported")
    if proposal.transition_type == "continue" or not proposal.thread_id or not proposal.pov:
        raise DirectorProposalError("parse", "A transition needs a type, thread and viewpoint mode", repairable=True)
    if proposal.pov not in {"cinematic", "omniscient"} and not viewpoint:
        raise DirectorProposalError("reference", "Limited or first-person scenes need a viewpoint character")
    if proposal.new_thread is not None:
        if proposal.new_thread.thread_id != proposal.thread_id or proposal.thread_id in threads:
            raise DirectorProposalError("reference", "New thread identity conflicts with the proposed scene")
    elif proposal.thread_id not in threads:
        raise DirectorProposalError("reference", "Director thread is not established or explicitly proposed")
