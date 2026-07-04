from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

from loguru import logger
from pydantic import ValidationError

from groupadmin.config import AppConfig
from groupadmin.models import OneBotRequest, ScheduledWholeUnmute, WholeMuteAction, WholeUnmuteAction
from groupadmin.onebot import OneBotAdapter


RequestSender = Callable[[str], Awaitable[None]]
PendingActionRecorder = Callable[[WholeUnmuteAction], None]


class WholeMuteScheduler:
    """全体禁言自动解除的持久化调度器。"""

    def __init__(self, config: AppConfig, onebot: OneBotAdapter, record_pending_action: PendingActionRecorder) -> None:
        self.path = config.scheduled_tasks_file
        self.onebot = onebot
        self.record_pending_action = record_pending_action
        self._sender: RequestSender | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def attach_sender(self, sender: RequestSender) -> None:
        self._sender = sender
        # 只有反向 WebSocket 连接存在时才能恢复调度，
        # 因为这是发请求回 OneBot 协议端的唯一通道。
        self._schedule_all()

    def detach_sender(self, sender: RequestSender) -> None:
        if self._sender is sender:
            self._sender = None

    def schedule(self, action: WholeMuteAction) -> None:
        if action.duration is None:
            self.cancel(action.group_id)
            return

        # 每个群只保留一个全体禁言自动解除任务；
        # 后一次带时间的指令会用新的结束时间覆盖旧任务。
        record = ScheduledWholeUnmute(
            group_id=action.group_id,
            execute_at=int(time.time()) + action.duration,
            source_event_id=action.event_id,
        )
        records = [item for item in self._load() if item.group_id != action.group_id]
        records.append(record)
        self._save(records)
        self._schedule_record(record)
        logger.info(f"已持久化全体禁言自动解除任务：群 {action.group_id}，执行时间戳 {record.execute_at}。")

    def cancel(self, group_id: int) -> None:
        records = [item for item in self._load() if item.group_id != group_id]
        self._save(records)
        task = self._tasks.pop(group_id, None)
        if task:
            task.cancel()

    def complete(self, group_id: int) -> None:
        self.cancel(group_id)

    def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    def _schedule_all(self) -> None:
        for record in self._load():
            self._schedule_record(record)

    def _schedule_record(self, record: ScheduledWholeUnmute) -> None:
        if self._sender is None:
            return
        old_task = self._tasks.pop(record.group_id, None)
        if old_task:
            old_task.cancel()
        self._tasks[record.group_id] = asyncio.create_task(self._run_record(record, self._sender))

    async def _run_record(self, record: ScheduledWholeUnmute, sender: RequestSender) -> None:
        delay = max(0, record.execute_at - int(time.time()))
        if delay:
            await asyncio.sleep(delay)

        action = WholeUnmuteAction(
            event_id=f"{record.source_event_id}:auto_whole_unmute",
            group_id=record.group_id,
            automatic=True,
        )
        request = self.onebot.build_request(None, action)
        if request is None:
            return

        try:
            self.record_pending_action(action)
            await sender(self._dump_request(request))
        except Exception as e:
            # 发送失败时保留持久化任务，后续连接恢复或重启后还能重试。
            logger.warning(f"发送群 {record.group_id} 的全体禁言自动解除请求失败，计划将保留：{e}")
            return

        # 不在这里删除计划；必须等 OneBot 返回 ok，app 才会调用 complete()。
        logger.info(f"已发送群 {record.group_id} 的全体禁言自动解除请求，等待协议端确认。")

    def _load(self) -> list[ScheduledWholeUnmute]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw_records = json.load(f)
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取计划任务失败：{e}")
            return []
        if not isinstance(raw_records, list):
            return []

        records: list[ScheduledWholeUnmute] = []
        for raw_record in raw_records:
            try:
                records.append(ScheduledWholeUnmute.model_validate(raw_record))
            except ValidationError as e:
                logger.warning(f"忽略不合法的计划任务记录：{e}")
        return records

    def _save(self, records: list[ScheduledWholeUnmute]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump([record.model_dump(mode="json") for record in records], f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.path)
        except OSError as e:
            logger.warning(f"保存计划任务失败：{e}")

    @staticmethod
    def _dump_request(request: OneBotRequest) -> str:
        return json.dumps(request.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
