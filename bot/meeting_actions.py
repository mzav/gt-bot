"""Meeting action button resolution for list/detail keyboards."""
from __future__ import annotations

from typing import Literal

WaitlistState = Literal["waiting", "offered"] | None


def resolve_meeting_actions(
    *,
    is_host: bool,
    is_participant: bool,
    available: int,
    waitlist_state: WaitlistState,
    include_register: bool,
) -> str:
    """Return action key for keyboard builder."""
    if is_host:
        return "host"
    if is_participant:
        return "participant"
    if include_register and available > 0:
        return "register"
    if waitlist_state == "waiting":
        return "waitlist_waiting"
    if waitlist_state == "offered":
        return "waitlist_offered"
    if include_register and available <= 0:
        return "waitlist_join"
    return "guest"
