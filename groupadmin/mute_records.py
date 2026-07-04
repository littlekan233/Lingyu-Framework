from __future__ import annotations

import json
import time

from loguru import logger
from pydantic import ValidationError

from groupadmin.config import AppConfig
from groupadmin.models import MutedMemberRecord


class MuteRecordStore:
    """当前禁言状态，用于成员退群重进后恢复禁言。"""

    def __init__(self, config: AppConfig) -> None:
        self.path = config.muted_members_file

    def upsert(self, group_id: int, user_id: int, duration: int, source_event_id: str) -> None:
        record = MutedMemberRecord(
            group_id=group_id,
            user_id=user_id,
            end_at=int(time.time()) + duration,
            source_event_id=source_event_id,
        )
        records = [
            item
            for item in self._prune(self._load())
            if not (item.group_id == group_id and item.user_id == user_id)
        ]
        records.append(record)
        self._save(records)

    def remove(self, group_id: int, user_id: int) -> None:
        records = [
            item
            for item in self._prune(self._load())
            if not (item.group_id == group_id and item.user_id == user_id)
        ]
        self._save(records)

    def get_active(self, group_id: int, user_id: int) -> MutedMemberRecord | None:
        # 读取时顺手清理过期记录，避免重启后恢复早已结束的禁言。
        records = self._prune(self._load())
        self._save(records)
        for record in records:
            if record.group_id == group_id and record.user_id == user_id:
                return record
        return None

    def remaining_seconds(self, record: MutedMemberRecord) -> int:
        return max(0, record.end_at - int(time.time()))

    def _prune(self, records: list[MutedMemberRecord]) -> list[MutedMemberRecord]:
        now = int(time.time())
        return [record for record in records if record.end_at > now]

    def _load(self) -> list[MutedMemberRecord]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw_records = json.load(f)
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取禁言记录失败：{e}")
            return []
        if not isinstance(raw_records, list):
            return []

        records: list[MutedMemberRecord] = []
        for raw_record in raw_records:
            try:
                records.append(MutedMemberRecord.model_validate(raw_record))
            except ValidationError as e:
                logger.warning(f"忽略不合法的禁言记录：{e}")
        return records

    def _save(self, records: list[MutedMemberRecord]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump([record.model_dump(mode="json") for record in records], f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.path)
        except OSError as e:
            logger.warning(f"保存禁言记录失败：{e}")
