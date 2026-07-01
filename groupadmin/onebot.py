from __future__ import annotations

from groupadmin.models import (
    CommandAction,
    AutoMuteAction,
    EssenceAction,
    IgnoreAction,
    KickAction,
    MessageEvent,
    MuteAction,
    OneBotRequest,
    RecallAction,
    ReplyAction,
    UnmuteAction,
    WholeMuteAction,
    WholeUnmuteAction,
)


AUDITED_ACTION_TYPES = {"recall", "mute", "unmute", "whole_mute", "whole_unmute", "kick", "essence"}


class OneBotAdapter:
    def build_request(self, event: MessageEvent | None, action: CommandAction) -> OneBotRequest | None:
        if isinstance(action, IgnoreAction):
            return None
        if isinstance(action, ReplyAction):
            if event is None:
                return None
            return OneBotRequest(
                action="send_group_msg",
                params={
                    "group_id": event.group_id,
                    "message": [
                        {"type": "reply", "data": {"id": str(event.message_id)}},
                        {"type": "text", "data": {"text": action.message}},
                    ],
                },
                echo=action.event_id,
            )
        if isinstance(action, EssenceAction):
            return OneBotRequest(
                action="set_essence_msg",
                params={"message_id": action.message_id},
                echo=action.event_id,
            )
        if isinstance(action, KickAction):
            if event is None:
                return None
            return OneBotRequest(
                action="set_group_kick",
                params={"group_id": str(event.group_id), "user_id": str(action.user_id)},
                echo=action.event_id,
            )
        if isinstance(action, MuteAction):
            if event is None:
                return None
            return OneBotRequest(
                action="set_group_ban",
                params={
                    "group_id": str(event.group_id),
                    "user_id": str(action.user_id),
                    "duration": action.duration,
                },
                echo=action.event_id,
            )
        if isinstance(action, UnmuteAction):
            if event is None:
                return None
            return OneBotRequest(
                action="set_group_ban",
                params={"group_id": str(event.group_id), "user_id": str(action.user_id), "duration": 0},
                echo=action.event_id,
            )
        if isinstance(action, AutoMuteAction):
            return OneBotRequest(
                action="set_group_ban",
                params={
                    "group_id": str(action.group_id),
                    "user_id": str(action.user_id),
                    "duration": action.duration,
                },
                echo=action.event_id,
            )
        if isinstance(action, WholeMuteAction):
            return OneBotRequest(
                action="set_group_whole_ban",
                params={"group_id": str(action.group_id), "enable": True},
                echo=action.event_id,
            )
        if isinstance(action, WholeUnmuteAction):
            return OneBotRequest(
                action="set_group_whole_ban",
                params={"group_id": str(action.group_id), "enable": False},
                echo=action.event_id,
            )
        if isinstance(action, RecallAction):
            return OneBotRequest(
                action="delete_msg",
                params={"message_id": action.message_id},
                echo=action.event_id,
            )
        return None

    def build_group_at_message(self, group_id: int, user_id: int, text: str, echo: str) -> OneBotRequest:
        return OneBotRequest(
            action="send_group_msg",
            params={
                "group_id": group_id,
                "message": [
                    {"type": "at", "data": {"qq": str(user_id)}},
                    {"type": "text", "data": {"text": text}},
                ],
            },
            echo=echo,
        )

    def build_group_member_list_request(self, group_id: int, echo: str) -> OneBotRequest:
        return OneBotRequest(
            action="get_group_member_list",
            params={"group_id": str(group_id)},
            echo=echo,
        )

    def build_group_owner_reminder(self, group_id: int, owner_ids: list[int], echo: str) -> OneBotRequest:
        message = []
        for owner_id in owner_ids:
            message.append({"type": "at", "data": {"qq": str(owner_id)}})
        message.append(
            {
                "type": "text",
                "data": {"text": " 自动解除全体禁言失败，Bot 可能缺少管理权限，请手动解除全体禁言。"},
            }
        )
        return OneBotRequest(
            action="send_group_msg",
            params={"group_id": group_id, "message": message},
            echo=echo,
        )
