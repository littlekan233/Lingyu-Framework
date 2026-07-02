from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from secrets import token_urlsafe
from typing import Any

from loguru import logger
from pydantic import ValidationError

from groupadmin.audit import AuditLog
from groupadmin.commands import CommandService
from groupadmin.config import AppConfig
from groupadmin.models import (
    ApiResponse,
    AutoMuteAction,
    EssenceAction,
    GroupIncreaseNotice,
    LifecycleEvent,
    MessageEvent,
    MuteAction,
    OneBotRequest,
    RecallAction,
    ReplyAction,
    UnmuteAction,
    WholeMuteAction,
    WholeUnmuteAction,
)
from groupadmin.mute_records import MuteRecordStore
from groupadmin.onebot import AUDITED_ACTION_TYPES, OneBotAdapter
from groupadmin.scheduler import WholeMuteScheduler
from groupadmin.store import EventStore

RequestSender = Callable[[str], Awaitable[None]]


class GroupAdminApp:
    def __init__(self, config: AppConfig) -> None:
        self.store = EventStore()
        self.audit_log = AuditLog(config)
        self.command_service = CommandService(config, self.audit_log)
        self.onebot = OneBotAdapter()
        self.whole_mute_scheduler = WholeMuteScheduler(config, self.onebot, self.store.mark_pending_audit)
        self.mute_records = MuteRecordStore(config)
        self.config = config

    async def handle_text(self, payload: str, request_sender: RequestSender | None = None) -> str | None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning(f"收到无效 JSON：{e}")
            return None
        if not isinstance(event, dict):
            logger.warning("收到的 JSON 不是 OneBot 事件对象。")
            return None

        if self._is_api_response(event):
            await self._handle_api_response(event, request_sender)
            return None
        if event.get("post_type") == "meta_event":
            self._handle_meta_event(event)
            return None
        if event.get("post_type") == "notice":
            await self._handle_notice_event(event, request_sender)
            return None
        if event.get("post_type") != "message":
            return None

        try:
            message_event = MessageEvent.model_validate(event)
        except ValidationError as e:
            logger.warning(f"消息事件结构不合法：{e}")
            return None

        event_id = self.store.add(message_event)
        logger.debug(f"收到消息事件（ID：{event_id}），raw_message：{message_event.raw_message}")
        action = self.command_service.process(event_id, message_event)
        if self._needs_target_message_lookup(action):
            lookup_echo = f"{event_id}:target_message"
            self.store.mark_pending_target_message_lookup(lookup_echo, action)
            request = self.onebot.build_message_lookup_request(action.message_id, lookup_echo)
            return self._dump_request(request)

        request = self.onebot.build_request(message_event, action)
        if request is None:
            logger.debug(f"ID 为 {event_id} 的事件无需处理。")
            self.store.remove(event_id)
            return None

        # OneBot 回包只靠 echo 关联请求，所以要先保存本地上下文，
        # 等协议端确认成功后再做审计、持久化等副作用。
        if action.type.value in AUDITED_ACTION_TYPES:
            self.store.mark_pending_audit(action)
        if isinstance(action, ReplyAction) and action.auto_recall_after:
            self.store.mark_pending_prompt_recall(action.event_id, action.auto_recall_after)
        return self._dump_request(request)

    def attach_request_sender(self, request_sender: RequestSender) -> None:
        self.whole_mute_scheduler.attach_sender(request_sender)

    def detach_request_sender(self, request_sender: RequestSender) -> None:
        self.whole_mute_scheduler.detach_sender(request_sender)

    def close(self) -> None:
        self.whole_mute_scheduler.close()

    async def _handle_api_response(self, event: dict[str, Any], request_sender: RequestSender | None) -> None:
        try:
            response = ApiResponse.model_validate(event)
        except ValidationError as e:
            logger.warning(f"API 响应结构不合法：{e}")
            return
        if not response.echo:
            return

        # 临时提示先作为普通群消息发出；只有 send_group_msg 回包带回
        # message_id 后，才能安排后续撤回。
        prompt_recall_delay = self.store.pop_pending_prompt_recall(response.echo)
        if prompt_recall_delay is not None:
            if response.status == "ok":
                self._schedule_prompt_recall(response, prompt_recall_delay, request_sender)
            else:
                logger.error(f"临时提示发送失败！协议端返回：{event}")
            self.store.remove(response.echo)
            return

        owner_reminder_group_id = self.store.pop_pending_owner_reminder(response.echo)
        if owner_reminder_group_id is not None:
            await self._handle_owner_lookup_response(owner_reminder_group_id, response, request_sender)
            return

        target_lookup_action = self.store.pop_pending_target_message_lookup(response.echo)
        if target_lookup_action is not None:
            await self._handle_target_message_lookup_response(target_lookup_action, response, request_sender)
            return

        pending_action = self.store.pop_pending_audit(response.echo)
        original_event = self.store.get(response.echo)
        if response.status == "ok":
            if pending_action and original_event:
                self.audit_log.record(original_event, pending_action)
                self._sync_mute_record(original_event, pending_action)
            elif pending_action:
                # 自动动作没有原始指令消息，例如成员退群重进后的恢复禁言，
                # 但仍然需要进入审计记录。
                self.audit_log.record_automatic(pending_action)
            if isinstance(pending_action, WholeMuteAction) and pending_action.duration and request_sender:
                self.whole_mute_scheduler.schedule(pending_action)
            if isinstance(pending_action, WholeUnmuteAction) and not pending_action.automatic:
                self.whole_mute_scheduler.cancel(pending_action.group_id)
            if isinstance(pending_action, WholeUnmuteAction) and pending_action.automatic:
                # 自动解除只有收到成功回包才算完成；发送请求本身不能删除计划。
                self.whole_mute_scheduler.complete(pending_action.group_id)
            logger.success(f"ID 为 {response.echo} 的事件处理成功desuwa！")
        else:
            logger.error(f"ID 为 {response.echo} 的事件处理失败！协议端返回：{event}")
            if (
                isinstance(pending_action, WholeUnmuteAction)
                and pending_action.automatic
                and request_sender
                and self._looks_like_permission_error(event)
            ):
                # 计划保留在文件里，同时提醒群主人工处理权限不足导致的失败。
                await self._request_owner_reminder(pending_action.group_id, request_sender)
            if original_event and request_sender:
                await self._send_temporary_prompt(
                    original_event,
                    f"指令执行失败：{self._format_api_error(event)}",
                    request_sender,
                )
        self.store.remove(response.echo)

    async def _request_owner_reminder(self, group_id: int, request_sender: RequestSender) -> None:
        echo = f"owner_reminder:{token_urlsafe(6)}"
        request = self.onebot.build_group_member_list_request(group_id, echo)
        # 先查群成员列表，拿到 role=owner 的 QQ 后才能真正 @群主。
        self.store.mark_pending_owner_reminder(echo, group_id)
        await request_sender(self._dump_request(request))

    async def _handle_target_message_lookup_response(
        self,
        action: RecallAction | EssenceAction,
        response: ApiResponse,
        request_sender: RequestSender | None,
    ) -> None:
        original_event = self.store.get(action.event_id)
        if original_event is None:
            return
        if request_sender is None:
            self.store.remove(action.event_id)
            return

        if response.status == "ok":
            action.target_summary = self.audit_log.summarize_message_data(response.data, str(action.message_id))
        else:
            logger.warning(f"获取消息 {action.message_id} 的内容失败，将使用消息 ID 写入审计：{response.model_dump()}")

        request = self.onebot.build_request(original_event, action)
        if request is None:
            self.store.remove(action.event_id)
            return

        self.store.mark_pending_audit(action)
        await request_sender(self._dump_request(request))

    async def _handle_owner_lookup_response(
        self,
        group_id: int,
        response: ApiResponse,
        request_sender: RequestSender | None,
    ) -> None:
        if request_sender is None:
            return
        if response.status != "ok":
            logger.warning(f"无法获取群 {group_id} 的群主信息，不能发送 @群主 提醒：{response.model_dump()}")
            return

        owner_ids = self._extract_owner_ids(response.data)
        if not owner_ids:
            logger.warning(f"群 {group_id} 的成员列表中没有找到群主，不能发送 @群主 提醒。")
            return

        request = self.onebot.build_group_owner_reminder(
            group_id=group_id,
            owner_ids=owner_ids,
            echo=f"owner_reminder_message:{token_urlsafe(6)}",
        )
        await request_sender(self._dump_request(request))

    async def _send_temporary_prompt(
        self,
        event: MessageEvent,
        message: str,
        request_sender: RequestSender,
        auto_recall_after: int = 10,
    ) -> None:
        action = ReplyAction(
            event_id=f"{token_urlsafe(6)}:temporary_prompt",
            message=message,
            auto_recall_after=auto_recall_after,
        )
        request = self.onebot.build_request(event, action)
        if request is None:
            return
        self.store.mark_pending_prompt_recall(action.event_id, auto_recall_after)
        await request_sender(self._dump_request(request))

    async def _handle_notice_event(self, event: dict[str, Any], request_sender: RequestSender | None) -> None:
        if event.get("notice_type") != "group_increase":
            return
        try:
            notice = GroupIncreaseNotice.model_validate(event)
        except ValidationError as e:
            logger.warning(f"入群通知结构不合法：{e}")
            return
        if notice.group_id not in self.config.group_whitelist:
            return

        record = self.mute_records.get_active(notice.group_id, notice.user_id)
        if record is None:
            return

        # 用户退群重进可能清掉平台侧禁言状态；持久化的结束时间
        # 可以让我们只恢复剩余时长。
        remaining = self.mute_records.remaining_seconds(record)
        if remaining <= 0:
            self.mute_records.remove(notice.group_id, notice.user_id)
            return
        if request_sender is None:
            logger.warning("检测到需要恢复的禁言记录，但当前没有可用的 OneBot 发送器。")
            return

        echo_prefix = f"auto_mute:{token_urlsafe(6)}"
        notify = self.onebot.build_group_at_message(
            group_id=notice.group_id,
            user_id=notice.user_id,
            text=f" 仍在禁言期内，已按原记录重新禁言，剩余 {self._format_duration(remaining)}。",
            echo=f"{echo_prefix}:notice",
        )
        action = AutoMuteAction(
            event_id=f"{echo_prefix}:ban",
            group_id=notice.group_id,
            user_id=notice.user_id,
            duration=remaining,
        )
        mute_request = self.onebot.build_request(None, action)
        if mute_request is None:
            return

        # at 提示只是说明原因，不进审计；真正恢复的禁言动作才进审计。
        self.store.mark_pending_audit(action)
        await request_sender(self._dump_request(notify))
        await request_sender(self._dump_request(mute_request))
        logger.info(f"检测到用户 {notice.user_id} 重新入群，已按剩余 {remaining} 秒恢复禁言。")

    def _sync_mute_record(self, event: MessageEvent, action: object) -> None:
        if event.group_id is None:
            return
        # 只有 OneBot 确认禁言成功后才记录预期结束时间，
        # 否则失败的指令会污染后续入群恢复逻辑。
        if isinstance(action, MuteAction):
            self.mute_records.upsert(
                group_id=event.group_id,
                user_id=action.user_id,
                duration=action.duration,
                source_event_id=action.event_id,
            )
        elif isinstance(action, UnmuteAction):
            self.mute_records.remove(event.group_id, action.user_id)

    @staticmethod
    def _handle_meta_event(event: dict[str, Any]) -> None:
        try:
            lifecycle = LifecycleEvent.model_validate(event)
        except ValidationError:
            return
        if lifecycle.meta_event_type == "lifecycle" and lifecycle.sub_type == "connect":
            logger.success(f"已连接到 OneBot {lifecycle.self_id}！")

    @staticmethod
    def _is_api_response(event: dict[str, Any]) -> bool:
        return bool(event.get("status") and "retcode" in event)

    @staticmethod
    def _needs_target_message_lookup(action: object) -> bool:
        return isinstance(action, RecallAction | EssenceAction) and action.target_summary is None

    @staticmethod
    def _dump_request(request: OneBotRequest) -> str:
        return json.dumps(request.model_dump(mode="json", exclude_none=True), ensure_ascii=False)

    def _schedule_prompt_recall(
        self,
        response: ApiResponse,
        delay_seconds: int,
        request_sender: RequestSender | None,
    ) -> None:
        if request_sender is None:
            return
        message_id = self._response_message_id(response)
        if message_id is None:
            logger.warning(f"临时提示发送成功，但回包里没有 message_id，无法自动撤回：{response.model_dump()}")
            return
        # 提示撤回只是清理刷屏，不做持久化；真正影响管理状态的定时器
        # 会由各自的持久化 store 保存。
        asyncio.create_task(self._recall_prompt_after_delay(message_id, delay_seconds, request_sender))

    async def _recall_prompt_after_delay(
        self,
        message_id: int,
        delay_seconds: int,
        request_sender: RequestSender,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        request = self.onebot.build_request(
            None,
            RecallAction(event_id=f"{token_urlsafe(6)}:recall_prompt", message_id=message_id),
        )
        if request is None:
            return
        await request_sender(self._dump_request(request))

    @staticmethod
    def _response_message_id(response: ApiResponse) -> int | None:
        if not isinstance(response.data, dict):
            return None
        try:
            return int(response.data.get("message_id"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_owner_ids(data: object) -> list[int]:
        if not isinstance(data, list):
            return []
        owner_ids: list[int] = []
        for item in data:
            if not isinstance(item, dict) or item.get("role") != "owner":
                continue
            try:
                owner_ids.append(int(item["user_id"]))
            except (KeyError, TypeError, ValueError):
                continue
        return owner_ids

    @staticmethod
    def _looks_like_permission_error(event: dict[str, Any]) -> bool:
        text = " ".join(str(event.get(key, "")) for key in ("wording", "message", "msg")).lower()
        retcode = event.get("retcode")
        permission_words = ("权限", "permission", "not admin", "not enough", "denied", "forbidden")
        return any(word in text for word in permission_words) or retcode in {100, 1400, 1401, 1403}

    @staticmethod
    def _format_api_error(event: dict[str, Any]) -> str:
        wording = event.get("wording") or event.get("message")
        retcode = event.get("retcode")
        if wording and retcode is not None:
            return f"{wording}（retcode: {retcode}）"
        if wording:
            return str(wording)
        if retcode is not None:
            return f"retcode: {retcode}"
        return "协议端返回失败。"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        parts = []
        for unit, unit_seconds in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
            value, seconds = divmod(seconds, unit_seconds)
            if value:
                parts.append(f"{value}{unit}")
        return "".join(parts) or "0秒"
