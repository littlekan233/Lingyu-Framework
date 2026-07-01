from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

from groupadmin.config import AppConfig
from groupadmin.models import (
    AutoMuteAction,
    CommandAction,
    EssenceAction,
    KickAction,
    MessageEvent,
    MuteAction,
    RecallAction,
    UnmuteAction,
    WholeMuteAction,
    WholeUnmuteAction,
)


class AuditLog:
    def __init__(self, config: AppConfig) -> None:
        self.path = config.audit_log_file
        self.retention_seconds = config.audit_retention_seconds

    def record(self, event: MessageEvent, action: CommandAction) -> None:
        if event.group_id is None:
            return
        records = self._prune(self._load())
        records.append(
            {
                "timestamp": int(time.time()),
                "group_id": event.group_id,
                "operator_id": event.sender.user_id,
                "operator_name": event.sender.card or event.sender.nickname or str(event.sender.user_id),
                "message_id": event.message_id,
                "type": action.type.value,
                "target_id": self._target_id(action),
                "duration": self._duration(action),
                "description": self._describe(action),
            }
        )
        self._save(records)

    def record_automatic(self, action: CommandAction) -> None:
        group_id = self._group_id(action)
        if group_id is None:
            return
        records = self._prune(self._load())
        records.append(
            {
                "timestamp": int(time.time()),
                "group_id": group_id,
                "operator_id": 0,
                "operator_name": "自动处理",
                "message_id": 0,
                "type": action.type.value,
                "target_id": self._target_id(action),
                "duration": self._duration(action),
                "description": self._describe(action),
            }
        )
        self._save(records)

    def build_group_message(self, group_id: int) -> str:
        records = self._prune(self._load())
        self._save(records)
        group_records = [record for record in records if int(record.get("group_id", 0)) == group_id]
        if not group_records:
            return "当前群最近两周没有操作记录。"

        lines = ["当前群最近两周操作记录："]
        for record in group_records:
            lines.append(
                f"{self._format_time(int(record['timestamp']))} "
                f"{record.get('operator_name', record.get('operator_id'))}({record.get('operator_id')}) "
                f"{record.get('description', record.get('type'))}"
            )
        return "\n".join(lines)

    def _load(self) -> list[dict]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                records = json.load(f)
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取操作记录失败：{e}")
            return []
        return records if isinstance(records, list) else []

    def _save(self, records: list[dict]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.path)
        except OSError as e:
            logger.warning(f"保存操作记录失败：{e}")

    def _prune(self, records: list[dict], now: int | None = None) -> list[dict]:
        now = now or int(time.time())
        min_ts = now - self.retention_seconds
        return [record for record in records if int(record.get("timestamp", 0)) >= min_ts]

    @staticmethod
    def _format_time(ts: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if seconds <= 0:
            return "0秒"
        parts = []
        for unit, unit_seconds in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
            value, seconds = divmod(seconds, unit_seconds)
            if value:
                parts.append(f"{value}{unit}")
        return "".join(parts)

    def _describe(self, action: CommandAction) -> str:
        if isinstance(action, RecallAction):
            return f"撤回消息 {action.message_id}"
        if isinstance(action, EssenceAction):
            return f"设置精华消息 {action.message_id}"
        if isinstance(action, MuteAction):
            return f"禁言 {action.user_id} {self._format_duration(action.duration)}"
        if isinstance(action, AutoMuteAction):
            return f"自动重新禁言 {action.user_id} {self._format_duration(action.duration)}"
        if isinstance(action, UnmuteAction):
            return f"解除禁言 {action.user_id}"
        if isinstance(action, WholeMuteAction):
            if action.duration:
                return f"全体禁言 {self._format_duration(action.duration)}"
            return "全体禁言"
        if isinstance(action, WholeUnmuteAction):
            return "解除全体禁言"
        if isinstance(action, KickAction):
            return f"踢出 {action.user_id}"
        return action.type.value

    @staticmethod
    def _target_id(action: CommandAction) -> int | None:
        if isinstance(action, RecallAction | EssenceAction):
            return action.message_id
        if isinstance(action, MuteAction | UnmuteAction | AutoMuteAction | KickAction):
            return action.user_id
        return None

    @staticmethod
    def _duration(action: CommandAction) -> int | None:
        return action.duration if isinstance(action, MuteAction | AutoMuteAction | WholeMuteAction) else None

    @staticmethod
    def _group_id(action: CommandAction) -> int | None:
        if isinstance(action, AutoMuteAction | WholeMuteAction | WholeUnmuteAction):
            return action.group_id
        return None
