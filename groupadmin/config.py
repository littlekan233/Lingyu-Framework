from __future__ import annotations

import json
from os import environ
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, field_validator


class AppConfig(BaseModel):
    ws_host: str = "127.0.0.1"
    ws_port: int = 20721
    log_level: str = "INFO"
    group_whitelist: set[int] = Field(default_factory=set)
    command_permissions: dict[str, int] = Field(default_factory=dict)
    member_permission_override: dict[int, dict[int, int]] = Field(default_factory=dict)
    audit_log_file: Path = Path("audit_log.json")
    scheduled_tasks_file: Path = Path("scheduled_tasks.json")
    muted_members_file: Path = Path("muted_members.json")
    audit_retention_seconds: int = 14 * 24 * 60 * 60
    reload_interval: float = 1.0

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


def _split_env_items(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("expected a JSON list or a comma separated list")


def _parse_int_set(raw: str) -> set[int]:
    return {int(item) for item in _split_env_items(raw)}


def _parse_member_override(raw: str) -> dict[int, dict[int, int]]:
    overrides: dict[int, dict[int, int]] = {}
    for item in _split_env_items(raw):
        try:
            group_id, user_id, level = _parse_override_item(item)
        except ValueError as e:
            logger.warning(f"成员权限等级覆写项“{item}”不合法，已跳过：{e}")
            continue
        overrides.setdefault(group_id, {})[user_id] = level
    return overrides


def _parse_override_item(item: str) -> tuple[int, int, int]:
    parts = item.split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid member permission override: {item}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _env_int(source: dict[str, Any], name: str, default: int) -> int:
    return int(source.get(name, str(default)))


def _env_float(source: dict[str, Any], name: str, default: float) -> float:
    return float(source.get(name, str(default)))


def _permission_level(source: dict[str, Any], name: str, default: int) -> int:
    return min(2, max(0, _env_int(source, name, default)))


def _permission_config(source: dict[str, Any]) -> dict[str, int]:
    help_level = _permission_level(source, "PERM_HELP", 0)
    return {
        "recall": _permission_level(source, "PERM_RECALL", 1),
        "mute": _permission_level(source, "PERM_MUTE", 1),
        "unmute": _permission_level(source, "PERM_MUTE", 1),
        "kick": _permission_level(source, "PERM_KICK", 1),
        "essence": _permission_level(source, "PERM_ESSENCE", 0),
        "help": help_level,
        "audit": _permission_level(source, "PERM_AUDIT", help_level),
    }


def load_config(env: dict[str, Any] | None = None) -> AppConfig:
    source = environ if env is None else env
    return AppConfig(
        ws_host=str(source.get("WSR_HOST", "127.0.0.1")),
        ws_port=int(source.get("WSR_PORT", "20721")),
        log_level=str(source.get("LOGLEVEL") or source.get("LOG_LEVEL") or "INFO"),
        group_whitelist=_parse_int_set(str(source.get("GROUP_WHITELIST", ""))),
        command_permissions=_permission_config(source),
        member_permission_override=_parse_member_override(str(source.get("MEMBER_PERM_OVERRIDE", ""))),
        audit_log_file=Path(str(source.get("AUDIT_LOG_FILE", "audit_log.json"))),
        scheduled_tasks_file=Path(str(source.get("SCHEDULED_TASKS_FILE", "scheduled_tasks.json"))),
        muted_members_file=Path(str(source.get("MUTED_MEMBERS_FILE", "muted_members.json"))),
        reload_interval=_env_float(source, "GROUPADMIN_RELOAD_INTERVAL", 1.0),
    )
