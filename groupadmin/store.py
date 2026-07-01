from __future__ import annotations

from secrets import token_urlsafe

from groupadmin.models import CommandAction, MessageEvent


class EventStore:
    """按 OneBot echo 关联的内存状态。"""

    def __init__(self) -> None:
        self._events: dict[str, MessageEvent] = {}
        self._pending_audits: dict[str, CommandAction] = {}
        self._pending_prompt_recalls: dict[str, int] = {}
        self._pending_owner_reminders: dict[str, int] = {}

    def add(self, event: MessageEvent) -> str:
        while True:
            event_id = token_urlsafe(6)
            if event_id not in self._events:
                self._events[event_id] = event
                return event_id

    def get(self, event_id: str) -> MessageEvent | None:
        return self._events.get(event_id)

    def remove(self, event_id: str) -> None:
        self._events.pop(event_id, None)
        self._pending_audits.pop(event_id, None)

    def mark_pending_audit(self, action: CommandAction) -> None:
        self._pending_audits[action.event_id] = action

    def pop_pending_audit(self, event_id: str) -> CommandAction | None:
        return self._pending_audits.pop(event_id, None)

    def mark_pending_prompt_recall(self, echo: str, delay_seconds: int) -> None:
        self._pending_prompt_recalls[echo] = delay_seconds

    def pop_pending_prompt_recall(self, echo: str) -> int | None:
        return self._pending_prompt_recalls.pop(echo, None)

    def mark_pending_owner_reminder(self, echo: str, group_id: int) -> None:
        self._pending_owner_reminders[echo] = group_id

    def pop_pending_owner_reminder(self, echo: str) -> int | None:
        return self._pending_owner_reminders.pop(echo, None)
