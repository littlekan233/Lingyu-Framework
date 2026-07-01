from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MessageSegment(FlexibleModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class Sender(FlexibleModel):
    user_id: int
    role: str = "member"
    card: str | None = None
    nickname: str | None = None


class MessageEvent(FlexibleModel):
    post_type: Literal["message"]
    message_type: str
    group_id: int | None = None
    user_id: int
    message_id: int
    raw_message: str = ""
    sender: Sender
    message: list[MessageSegment] = Field(default_factory=list)


class LifecycleEvent(FlexibleModel):
    post_type: Literal["meta_event"]
    meta_event_type: str
    sub_type: str | None = None
    self_id: int | None = None


class GroupIncreaseNotice(FlexibleModel):
    post_type: Literal["notice"]
    notice_type: Literal["group_increase"]
    group_id: int
    user_id: int
    operator_id: int | None = None
    sub_type: str | None = None


class ApiResponse(FlexibleModel):
    status: str
    retcode: int | None = None
    echo: str | None = None
    data: Any = None


class ActionType(str, Enum):
    IGNORE = "ignore"
    REPLY = "reply"
    RECALL = "recall"
    MUTE = "mute"
    UNMUTE = "unmute"
    AUTO_MUTE = "auto_mute"
    WHOLE_MUTE = "whole_mute"
    WHOLE_UNMUTE = "whole_unmute"
    KICK = "kick"
    ESSENCE = "essence"


class BaseAction(BaseModel):
    event_id: str
    type: ActionType


class IgnoreAction(BaseAction):
    type: Literal[ActionType.IGNORE] = ActionType.IGNORE


class ReplyAction(BaseAction):
    type: Literal[ActionType.REPLY] = ActionType.REPLY
    message: str
    auto_recall_after: int | None = None


class RecallAction(BaseAction):
    type: Literal[ActionType.RECALL] = ActionType.RECALL
    message_id: int


class EssenceAction(BaseAction):
    type: Literal[ActionType.ESSENCE] = ActionType.ESSENCE
    message_id: int


class MuteAction(BaseAction):
    type: Literal[ActionType.MUTE] = ActionType.MUTE
    user_id: int
    duration: int


class UnmuteAction(BaseAction):
    type: Literal[ActionType.UNMUTE] = ActionType.UNMUTE
    user_id: int


class AutoMuteAction(BaseAction):
    type: Literal[ActionType.AUTO_MUTE] = ActionType.AUTO_MUTE
    group_id: int
    user_id: int
    duration: int
    reason: str = "自动处理"


class WholeMuteAction(BaseAction):
    type: Literal[ActionType.WHOLE_MUTE] = ActionType.WHOLE_MUTE
    group_id: int
    duration: int | None = None


class WholeUnmuteAction(BaseAction):
    type: Literal[ActionType.WHOLE_UNMUTE] = ActionType.WHOLE_UNMUTE
    group_id: int
    automatic: bool = False


class KickAction(BaseAction):
    type: Literal[ActionType.KICK] = ActionType.KICK
    user_id: int


CommandAction = (
    IgnoreAction
    | ReplyAction
    | RecallAction
    | EssenceAction
    | MuteAction
    | UnmuteAction
    | AutoMuteAction
    | WholeMuteAction
    | WholeUnmuteAction
    | KickAction
)


class OneBotRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    echo: str


class ScheduledWholeUnmute(BaseModel):
    group_id: int
    execute_at: int
    source_event_id: str


class MutedMemberRecord(BaseModel):
    group_id: int
    user_id: int
    end_at: int
    source_event_id: str
