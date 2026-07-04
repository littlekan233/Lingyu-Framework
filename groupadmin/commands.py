from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from groupadmin.audit import AuditLog
from groupadmin.config import AppConfig
from groupadmin.models import (
    CommandAction,
    EssenceAction,
    IgnoreAction,
    KickAction,
    MessageEvent,
    MuteAction,
    RecallAction,
    ReplyAction,
    UnmuteAction,
    WholeMuteAction,
    WholeUnmuteAction,
)


@dataclass(frozen=True)
class ParsedCommand:
    name: str | None
    target_message_id: int = 0
    target_user_id: int = 0
    target_all: bool = False
    mute_time: str = ""


class CommandService:
    def __init__(self, config: AppConfig, audit_log: AuditLog) -> None:
        self.config = config
        self.audit_log = audit_log

    def process(self, event_id: str, event: MessageEvent) -> CommandAction:
        if event.message_type != "group" or event.group_id is None:
            logger.debug("非群聊消息，pass")
            return IgnoreAction(event_id=event_id)
        if event.group_id not in self.config.group_whitelist:
            logger.debug("未在白名单，pass")
            return IgnoreAction(event_id=event_id)

        parsed = self._parse(event)
        if parsed.name is None:
            return IgnoreAction(event_id=event_id)

        sender_perm = self._user_permission_level(event)
        required_perm = self.config.command_permissions[parsed.name]
        logger.debug(f"will run command {parsed.name}, permlevel at least {required_perm}")
        if sender_perm < required_perm:
            logger.debug(f"perm not pass. sender perm: {sender_perm}")
            return self._reply(event_id, "权限不足，无法执行该指令。")

        return self._dispatch(event_id, event, parsed)

    def _parse(self, event: MessageEvent) -> ParsedCommand:
        command_name: str | None = None
        target_message_id = 0
        target_user_id = 0
        target_all = False
        text_segments: list[str] = []
        text_tokens: list[str] = []

        logger.debug(f"processing message, message_id: {event.message_id}")
        for segment in event.message:
            if segment.type == "reply":
                target_message_id = _to_int(segment.data.get("id"))
                logger.debug(f"found reply, target message_id: {target_message_id}")
            elif segment.type == "at":
                at_target = segment.data.get("qq")
                if at_target == "all":
                    target_all = True
                    logger.debug("found at all members")
                else:
                    target_user_id = _to_int(at_target)
                    logger.debug(f"found at, target user_id: {target_user_id}")
            elif segment.type == "text":
                text = str(segment.data.get("text", "")).strip()
                if not text:
                    continue
                text_segments.append(text)
                text_tokens.extend(text.split())

        command_name = _find_command_name(text_tokens, text_segments)
        if command_name in {"mute", "unmute"}:
            target_all = target_all or any(_is_all_keyword(token) for token in text_tokens)
            target_user_id = target_user_id or _find_target_user_id(text_tokens)

        mute_time = _find_mute_time(command_name, text_tokens, text_segments, target_user_id)

        return ParsedCommand(
            name=command_name,
            target_message_id=target_message_id,
            target_user_id=target_user_id,
            target_all=target_all,
            mute_time=mute_time,
        )

    def _user_permission_level(self, event: MessageEvent) -> int:
        if event.group_id is None:
            return 0
        group_overrides = self.config.member_permission_override.get(event.group_id, {})
        if event.sender.user_id in group_overrides:
            return group_overrides[event.sender.user_id]
        return {"owner": 2, "admin": 1, "member": 0}.get(event.sender.role, 0)

    def _dispatch(self, event_id: str, event: MessageEvent, parsed: ParsedCommand) -> CommandAction:
        if parsed.name == "recall":
            if parsed.target_message_id <= 0:
                return self._reply(event_id, "引用的消息无效。")
            return RecallAction(event_id=event_id, message_id=parsed.target_message_id)
        if parsed.name == "essence":
            if parsed.target_message_id <= 0:
                return self._reply(event_id, "引用的消息无效。")
            return EssenceAction(event_id=event_id, message_id=parsed.target_message_id)
        if parsed.name == "mute":
            if parsed.target_all and event.group_id is not None:
                if not parsed.mute_time:
                    return WholeMuteAction(event_id=event_id, group_id=event.group_id)
                try:
                    duration = parse_duration(parsed.mute_time)
                except ValueError:
                    return self._reply(event_id, "无效的时间格式。")
                return WholeMuteAction(event_id=event_id, group_id=event.group_id, duration=duration)
            if parsed.target_user_id <= 0:
                return self._reply(event_id, "目标用户不存在。")
            try:
                duration = parse_duration(parsed.mute_time)
            except ValueError:
                return self._reply(event_id, "无效的时间格式。")
            return MuteAction(event_id=event_id, user_id=parsed.target_user_id, duration=duration)
        if parsed.name == "unmute":
            if parsed.target_all and event.group_id is not None:
                return WholeUnmuteAction(event_id=event_id, group_id=event.group_id)
            if parsed.target_user_id <= 0:
                return self._reply(event_id, "目标用户不存在。")
            return UnmuteAction(event_id=event_id, user_id=parsed.target_user_id)
        if parsed.name == "kick":
            if parsed.target_user_id <= 0:
                return self._reply(event_id, "目标用户不存在。")
            return KickAction(event_id=event_id, user_id=parsed.target_user_id)
        if parsed.name == "help":
            return self._reply(event_id, _help_message(), auto_recall_after=None)
        if parsed.name == "audit" and event.group_id is not None:
            return self._reply(event_id, self.audit_log.build_group_message(event.group_id), auto_recall_after=None)
        return IgnoreAction(event_id=event_id)

    @staticmethod
    def _reply(event_id: str, message: str, auto_recall_after: int | None = 10) -> ReplyAction:
        return ReplyAction(event_id=event_id, message=message, auto_recall_after=auto_recall_after)


def parse_duration(value: str) -> int:
    value = value.strip().replace(" ", "")
    value = (
        value.replace("天", "d")
        .replace("day", "d")
        .replace("小时", "h")
        .replace("hour", "h")
        .replace("分钟", "m")
        .replace("分", "m")
        .replace("min", "m")
        .replace("sec", "s")
        .replace("秒", "s")
    )
    if not value or not re.fullmatch(r"(\d+[dhms]?)+", value):
        raise ValueError("invalid duration")

    seconds = 0
    for amount, unit in re.findall(r"(\d+)([dhms]?)", value):
        amount_int = int(amount)
        if unit == "d":
            seconds += amount_int * 86400
        elif unit == "h":
            seconds += amount_int * 3600
        elif unit == "m":
            seconds += amount_int * 60
        else:
            seconds += amount_int
    return seconds


def _to_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_all_keyword(value: object) -> bool:
    return str(value).strip().lower() in {"all", "全体", "所有人"}


def _find_command_name(text_tokens: list[str], text_segments: list[str]) -> str | None:
    for token in text_tokens:
        command_name = _COMMAND_ALIASES.get(token)
        if command_name:
            return command_name
    for text in text_segments:
        command_name = _COMMAND_ALIASES.get(text)
        if command_name:
            return command_name
    return None


def _find_target_user_id(text_tokens: list[str]) -> int:
    for token in text_tokens:
        if token in _COMMAND_ALIASES or _is_all_keyword(token):
            continue
        user_id = _to_int(token)
        if user_id > 0:
            return user_id
    return 0


def _find_mute_time(
    command_name: str | None,
    text_tokens: list[str],
    text_segments: list[str],
    target_user_id: int,
) -> str:
    if command_name != "mute":
        return ""
    skipped_target_user = False
    for token in text_tokens:
        if token in _COMMAND_ALIASES or _is_all_keyword(token):
            continue
        if target_user_id > 0 and not skipped_target_user and _to_int(token) == target_user_id:
            skipped_target_user = True
            continue
        return token
    for text in text_segments:
        if _COMMAND_ALIASES.get(text):
            continue
        tokens = text.split()
        if len(tokens) == 1 and not _is_all_keyword(tokens[0]) and _to_int(tokens[0]) <= 0:
            return tokens[0]
    return ""

def _help_message() -> str:
    return """[GroupAdmin] by littlekan233
https://github.com/littlekan233/groupadmin

命令帮助：
撤回：引用一条消息并发送 /recall 或者 /撤回
设为精华：引用一条消息并发送 /essence 或者 /设精
禁言：发送 /mute @xxx 时间 或者 /禁言 QQ号 时间；all/全体/所有人 表示全体禁言，可不带时间
解禁：发送 /unmute @xxx 或者 /解禁 QQ号；all/全体/所有人 表示解除全体禁言
踢人：发送 /kick @xxx 或者 /踢人 @xxx
操作记录：发送 /audit 或者 /操作记录

时间限制单位有天、小时、分钟、秒，可以组合出现。
天：d/day
小时：h/hour
分钟：m/min
秒：s/sec（默认）
上方单位两种写法混合出现也可以，能解析。

除非命令参数有误，命令执行后不会回复是否完成。
祝各位有一个清静的聊天环境w"""


_COMMAND_ALIASES = {
    "/recall": "recall",
    "/撤回": "recall",
    "/essence": "essence",
    "/设精": "essence",
    "/kick": "kick",
    "/踢人": "kick",
    "/mute": "mute",
    "/禁言": "mute",
    "/unmute": "unmute",
    "/解禁": "unmute",
    "/gahelp": "help",
    "/帮助": "help",
    "/audit": "audit",
    "/操作记录": "audit",
}
