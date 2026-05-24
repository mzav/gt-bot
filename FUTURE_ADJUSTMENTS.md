# Future Adjustments

Follow-up items from the `feature/change-and-cancel-my-meeting-buttons` review (2026-05-24).

## Fixed in branch

- `/my_meetings` showed edit/cancel to non-hosts
- Clearing location during edit (`-` / skip values)
- Canceled meetings appearing in `/my_meetings`
- Edit entry blocked for canceled meetings
- Dead `edit_field:back` handler

---

## Remaining

### Scheduler — remove reminder jobs on cancel

**Priority:** Medium (becomes High when reminders are implemented)

`cb_cancel_confirm` sets `canceled_at` but does not remove APScheduler jobs (`meeting_{id}_reminder_3`, `meeting_{id}_reminder_1`). `_reminder_job` is currently a no-op, so this is harmless today.

**Suggested change:** add `BotScheduler.cancel_meeting_reminders(meeting_id)` and call it from `cb_cancel_confirm`.

---

### Edit flow — narrow `user_data` cleanup

**Priority:** Low

`_edit_done` and `_edit_cancel` call `context.user_data.clear()`, which wipes all conversation state. Unlikely to collide with the create flow in practice, but edit-specific keys (`edit_meeting_id`, `edit_selected_*`) could be popped instead.

---

### Edit flow — guard canceled meetings mid-session

**Priority:** Low

`_edit_meeting_start` rejects canceled meetings. Field update handlers do not re-check `canceled_at` if the meeting was canceled while the user was editing.

---

### Storage — reject updates to canceled meetings

**Priority:** Low

Authorization and cancel checks live in handlers only. `update_meeting()` and `cancel_meeting()` do not enforce `canceled_at` at the DB layer.

---

### HTML escaping for user-provided text

**Priority:** Medium

Meeting topic, description, and location are inserted into HTML messages without escaping. Malformed or malicious input can break formatting or alter message layout. Pre-existing pattern; edit flow adds more surfaces.

**Suggested change:** small helper (e.g. `html.escape`) applied wherever user content is rendered with `parse_mode="HTML"`.

---

### Reduce duplicated meeting summary text

**Priority:** Low

The edit-menu summary block (topic, description, datetime, max, location) is repeated in many handlers. Extract a formatter to keep copy and layout in one place.

---

### Automated tests for edit/cancel flows

**Priority:** Medium

No tests cover the new conversation handler, cancel confirmation flow, host-only keyboard logic, or `update_meeting(clear_location=True)`.

**Suggested coverage:**

- Host vs non-host keyboard in `/upcoming_meetings` and `/my_meetings`
- Edit each field; clear location with `-`
- Cancel with confirm/abort
- Edit/cancel rejected for non-host, missing meeting, already-canceled meeting
- Canceled meetings excluded from `/my_meetings`

---

### Global error handler for callback failures

**Priority:** Medium

No `Application.add_error_handler` is registered. Unhandled exceptions in button handlers may be easy to miss at `LOG_LEVEL=INFO`. Useful for local debugging and production monitoring.

---

### README — mark edit/cancel as implemented

**Priority:** Low

README still lists edit/cancel as "planned" under Features and Commands. Update when the feature ships.
