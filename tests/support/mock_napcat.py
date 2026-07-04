from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from groupadmin.app import GroupAdminApp, RequestSender


ResponseOverride = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class MockNapCatServer:
    """A small OneBot/NapCat protocol simulator for integration tests.

    The real app listens for reverse WebSocket connections. In tests this class
    plays the protocol endpoint: it feeds events to the app, accepts action
    requests, mutates local QQ-like state, and returns OneBot-shaped responses.
    """

    self_id: int = 123456
    members: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    messages: dict[int, dict[str, Any]] = field(default_factory=dict)
    response_overrides: dict[str, ResponseOverride] = field(default_factory=dict)

    requests: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    group_messages: list[dict[str, Any]] = field(default_factory=list)
    group_bans: dict[tuple[int, int], int] = field(default_factory=dict)
    whole_bans: dict[int, bool] = field(default_factory=dict)
    kicked_members: list[dict[str, int]] = field(default_factory=list)
    deleted_messages: list[int] = field(default_factory=list)
    essence_messages: list[int] = field(default_factory=list)

    _next_message_id: int = 100000

    async def send_event(
        self,
        app: GroupAdminApp,
        event: dict[str, Any],
        sender: RequestSender | None = None,
    ) -> None:
        sender = sender or self.make_sender(app)
        response = await app.handle_text(_json(event), sender)
        if response:
            await sender(response)

    def connect(self, app: GroupAdminApp) -> RequestSender:
        sender = self.make_sender(app)
        app.attach_request_sender(sender)
        return sender

    @staticmethod
    def disconnect(app: GroupAdminApp, sender: RequestSender) -> None:
        app.detach_request_sender(sender)

    def make_sender(self, app: GroupAdminApp) -> RequestSender:
        async def send(payload: str) -> None:
            request = self._load_request(payload)
            self.requests.append(request)
            response = self.handle_request(request)
            if response is None:
                return

            self.responses.append(response)
            followup = await app.handle_text(_json(response), send)
            if followup:
                await send(followup)

        return send

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        action = str(request.get("action", ""))
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}
        echo = request.get("echo")

        override = self.response_overrides.get(action)
        if override is not None:
            response = override(request) if callable(override) else dict(override)
            response.setdefault("echo", echo)
            response.setdefault("data", None)
            return response

        handler = getattr(self, f"_handle_{action}", None)
        if handler is None:
            return self._failed(echo, 1404, f"Unsupported action: {action}")
        return handler(params, echo)

    def set_failed_action(self, action: str, retcode: int = 100, wording: str = "权限不足") -> None:
        self.response_overrides[action] = {
            "status": "failed",
            "retcode": retcode,
            "data": None,
            "wording": wording,
        }

    def _handle_send_group_msg(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        group_id = _int(params.get("group_id"))
        message_id = self._allocate_message_id()
        record = {
            "message_id": message_id,
            "group_id": group_id,
            "message": params.get("message", []),
        }
        self.group_messages.append(record)
        self.messages[message_id] = record
        return self._ok(echo, {"message_id": message_id})

    def _handle_set_group_ban(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        group_id = _int(params.get("group_id"))
        user_id = _int(params.get("user_id"))
        duration = _int(params.get("duration"))
        key = (group_id, user_id)
        if duration > 0:
            self.group_bans[key] = duration
        else:
            self.group_bans.pop(key, None)
        return self._ok(echo)

    def _handle_set_group_whole_ban(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        group_id = _int(params.get("group_id"))
        self.whole_bans[group_id] = bool(params.get("enable"))
        return self._ok(echo)

    def _handle_set_group_kick(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        self.kicked_members.append(
            {
                "group_id": _int(params.get("group_id")),
                "user_id": _int(params.get("user_id")),
            }
        )
        return self._ok(echo)

    def _handle_delete_msg(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        message_id = _int(params.get("message_id"))
        self.deleted_messages.append(message_id)
        return self._ok(echo)

    def _handle_set_essence_msg(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        message_id = _int(params.get("message_id"))
        self.essence_messages.append(message_id)
        return self._ok(echo)

    def _handle_get_msg(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        message_id = _int(params.get("message_id"))
        message = self.messages.get(message_id)
        if message is None:
            return self._failed(echo, 100, f"message {message_id} not found")
        return self._ok(echo, message)

    def _handle_get_group_member_list(self, params: dict[str, Any], echo: Any) -> dict[str, Any]:
        group_id = _int(params.get("group_id"))
        return self._ok(echo, list(self.members.get(group_id, [])))

    def _allocate_message_id(self) -> int:
        self._next_message_id += 1
        return self._next_message_id

    @staticmethod
    def _load_request(payload: str) -> dict[str, Any]:
        request = json.loads(payload)
        if not isinstance(request, dict):
            raise ValueError("OneBot request payload must be a JSON object")
        return request

    @staticmethod
    def _ok(echo: Any, data: Any = None) -> dict[str, Any]:
        return {"status": "ok", "retcode": 0, "data": data, "echo": echo}

    @staticmethod
    def _failed(echo: Any, retcode: int, wording: str) -> dict[str, Any]:
        return {"status": "failed", "retcode": retcode, "data": None, "echo": echo, "wording": wording}


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
