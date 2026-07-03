from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from groupadmin.app import GroupAdminApp
from groupadmin.config import AppConfig
from groupadmin.models import WholeUnmuteAction
from groupadmin.server import create_web_app
from tests.support.mock_napcat import MockNapCatServer


GROUP_ID = 1001
ADMIN_ID = 10001
MEMBER_ID = 20002
OWNER_ID = 30003


class MockNapCatModerationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = GroupAdminApp(_config(self.root))

    async def asyncTearDown(self) -> None:
        self.app.close()
        self.tmpdir.cleanup()

    async def test_mute_success_records_ban_and_audit(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/mute"), _at(MEMBER_ID), _text("2m")]))

        self.assertEqual(napcat.group_bans[(GROUP_ID, MEMBER_ID)], 120)
        self.assertEqual(_actions(napcat), ["set_group_ban"])
        self.assertEqual(_audit_types(self.root), ["mute"])

    async def test_unmute_success_clears_ban_and_audit(self) -> None:
        napcat = MockNapCatServer(group_bans={(GROUP_ID, MEMBER_ID): 120})

        await napcat.send_event(self.app, _group_message([_text("/unmute"), _at(MEMBER_ID)]))

        self.assertNotIn((GROUP_ID, MEMBER_ID), napcat.group_bans)
        self.assertEqual(_actions(napcat), ["set_group_ban"])
        self.assertEqual(_audit_types(self.root), ["unmute"])
        self.assertEqual(_muted_records(self.root), [])

    async def test_mute_bot_permission_denied_sends_temporary_prompt(self) -> None:
        napcat = MockNapCatServer()
        napcat.set_failed_action("set_group_ban", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_text("/mute"), _at(MEMBER_ID), _text("1m")]))

        self.assertEqual(_actions(napcat), ["set_group_ban", "send_group_msg"])
        self.assertNotIn((GROUP_ID, MEMBER_ID), napcat.group_bans)
        self.assertIn("指令执行失败", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])
        self.assertEqual(_muted_records(self.root), [])

    async def test_unmute_bot_permission_denied_keeps_ban_and_prompts(self) -> None:
        napcat = MockNapCatServer(group_bans={(GROUP_ID, MEMBER_ID): 120})
        napcat.set_failed_action("set_group_ban", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_text("/unmute"), _at(MEMBER_ID)]))

        self.assertEqual(napcat.group_bans[(GROUP_ID, MEMBER_ID)], 120)
        self.assertEqual(_actions(napcat), ["set_group_ban", "send_group_msg"])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_mute_operator_permission_denied_does_not_call_ban_api(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            _group_message([_text("/mute"), _at(MEMBER_ID), _text("1m")], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_mute_success_persists_muted_member_record_shape(self) -> None:
        napcat = MockNapCatServer()
        before = int(time.time())

        await napcat.send_event(self.app, _group_message([_text("/mute"), _at(MEMBER_ID), _text("60s")]))

        records = _muted_records(self.root)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["group_id"], GROUP_ID)
        self.assertEqual(record["user_id"], MEMBER_ID)
        self.assertGreaterEqual(record["end_at"], before + 60)
        self.assertLessEqual(record["end_at"], int(time.time()) + 60)
        self.assertIsInstance(record["source_event_id"], str)

    async def test_mute_duration_parsing_accepts_mixed_units(self) -> None:
        cases = {
            "1d2h3m4s": 93784,
            "2小时": 7200,
            "15分钟": 900,
            "45": 45,
        }
        for raw_duration, expected_seconds in cases.items():
            with self.subTest(raw_duration=raw_duration):
                app = GroupAdminApp(_config(self.root / raw_duration))
                napcat = MockNapCatServer()
                try:
                    await napcat.send_event(app, _group_message([_text("/mute"), _at(MEMBER_ID), _text(raw_duration)]))
                    self.assertEqual(napcat.requests[-1]["params"]["duration"], expected_seconds)
                    self.assertEqual(napcat.group_bans[(GROUP_ID, MEMBER_ID)], expected_seconds)
                finally:
                    app.close()

    async def test_group_increase_restores_active_mute_record(self) -> None:
        napcat = MockNapCatServer()
        self.app.mute_records.upsert(GROUP_ID, MEMBER_ID, 120, "seed")

        await napcat.send_event(
            self.app,
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "group_id": GROUP_ID,
                "user_id": MEMBER_ID,
                "operator_id": MEMBER_ID,
            },
        )

        self.assertEqual(_actions(napcat), ["send_group_msg", "set_group_ban"])
        self.assertGreater(napcat.group_bans[(GROUP_ID, MEMBER_ID)], 0)
        self.assertEqual(napcat.group_messages[-1]["message"][0], _at(MEMBER_ID))
        self.assertIn("仍在禁言期内", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), ["auto_mute"])

    async def test_malformed_muted_member_records_are_ignored(self) -> None:
        _write_json(self.root / "muted_members.json", [{"group_id": GROUP_ID}, "broken"])
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "group_id": GROUP_ID,
                "user_id": MEMBER_ID,
                "operator_id": MEMBER_ID,
            },
        )

        self.assertEqual(napcat.requests, [])
        self.assertEqual(_muted_records(self.root), [])


class MockNapCatWholeMuteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = GroupAdminApp(_config(self.root))

    async def asyncTearDown(self) -> None:
        self.app.close()
        self.tmpdir.cleanup()

    async def test_whole_mute_success(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/mute all")]))

        self.assertTrue(napcat.whole_bans[GROUP_ID])
        self.assertEqual(_actions(napcat), ["set_group_whole_ban"])
        self.assertEqual(_audit_types(self.root), ["whole_mute"])

    async def test_whole_unmute_success(self) -> None:
        napcat = MockNapCatServer(whole_bans={GROUP_ID: True})

        await napcat.send_event(self.app, _group_message([_text("/unmute all")]))

        self.assertFalse(napcat.whole_bans[GROUP_ID])
        self.assertEqual(_actions(napcat), ["set_group_whole_ban"])
        self.assertEqual(_audit_types(self.root), ["whole_unmute"])

    async def test_timed_whole_mute_auto_unmutes(self) -> None:
        napcat = MockNapCatServer()
        sender = napcat.connect(self.app)
        try:
            await napcat.send_event(self.app, _group_message([_text("/mute all 1s")]), sender)
            self.assertTrue(napcat.whole_bans[GROUP_ID])

            await asyncio.sleep(1.2)

            self.assertFalse(napcat.whole_bans[GROUP_ID])
            self.assertEqual(_actions(napcat), ["set_group_whole_ban", "set_group_whole_ban"])
            self.assertEqual(_scheduled_records(self.root), [])
        finally:
            napcat.disconnect(self.app, sender)

    async def test_timed_whole_mute_persists_schedule_record_shape(self) -> None:
        napcat = MockNapCatServer()
        before = int(time.time())

        await napcat.send_event(self.app, _group_message([_text("/mute all 60s")]))

        records = _scheduled_records(self.root)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["group_id"], GROUP_ID)
        self.assertGreaterEqual(record["execute_at"], before + 60)
        self.assertLessEqual(record["execute_at"], int(time.time()) + 60)
        self.assertIsInstance(record["source_event_id"], str)
        self.assertTrue(napcat.whole_bans[GROUP_ID])

    async def test_whole_mute_bot_permission_denied_prompts(self) -> None:
        napcat = MockNapCatServer()
        napcat.set_failed_action("set_group_whole_ban", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_text("/mute all")]))

        self.assertNotIn(GROUP_ID, napcat.whole_bans)
        self.assertEqual(_actions(napcat), ["set_group_whole_ban", "send_group_msg"])
        self.assertIn("指令执行失败", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_whole_unmute_bot_permission_denied_prompts(self) -> None:
        napcat = MockNapCatServer(whole_bans={GROUP_ID: True})
        napcat.set_failed_action("set_group_whole_ban", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_text("/unmute all")]))

        self.assertTrue(napcat.whole_bans[GROUP_ID])
        self.assertEqual(_actions(napcat), ["set_group_whole_ban", "send_group_msg"])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_automatic_whole_unmute_permission_revoked_mentions_owner(self) -> None:
        napcat = MockNapCatServer(
            members={
                GROUP_ID: [
                    {"group_id": GROUP_ID, "user_id": OWNER_ID, "role": "owner", "nickname": "Owner"},
                    {"group_id": GROUP_ID, "user_id": ADMIN_ID, "role": "admin", "nickname": "Admin"},
                ]
            }
        )
        action = WholeUnmuteAction(event_id="auto-unmute:1", group_id=GROUP_ID, automatic=True)
        self.app.store.mark_pending_audit(action)

        await self.app.handle_text(
            _json(
                {
                    "status": "failed",
                    "retcode": 1403,
                    "data": None,
                    "echo": action.event_id,
                    "wording": "权限不足",
                }
            ),
            napcat.make_sender(self.app),
        )

        self.assertEqual(_actions(napcat), ["get_group_member_list", "send_group_msg"])
        self.assertEqual(napcat.group_messages[-1]["message"][0], _at(OWNER_ID))
        self.assertIn("自动解除全体禁言失败", _last_group_text(napcat))

    async def test_automatic_whole_unmute_failure_keeps_schedule_record(self) -> None:
        _write_json(
            self.root / "scheduled_tasks.json",
            [{"group_id": GROUP_ID, "execute_at": int(time.time()), "source_event_id": "seed-event"}],
        )
        self.app.close()
        self.app = GroupAdminApp(_config(self.root))
        napcat = MockNapCatServer(
            whole_bans={GROUP_ID: True},
            members={GROUP_ID: [{"group_id": GROUP_ID, "user_id": OWNER_ID, "role": "owner"}]},
        )
        napcat.set_failed_action("set_group_whole_ban", retcode=1403, wording="权限不足")
        sender = napcat.connect(self.app)
        try:
            await asyncio.sleep(0.2)

            self.assertTrue(napcat.whole_bans[GROUP_ID])
            self.assertEqual(_actions(napcat), ["set_group_whole_ban", "get_group_member_list", "send_group_msg"])
            self.assertEqual(_scheduled_records(self.root)[0]["source_event_id"], "seed-event")
        finally:
            napcat.disconnect(self.app, sender)

    async def test_whole_mute_operator_permission_denied(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            _group_message([_text("/mute all")], user_id=MEMBER_ID, role="member"),
        )
        await napcat.send_event(
            self.app,
            _group_message([_text("/unmute all")], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg", "send_group_msg"])
        self.assertNotIn(GROUP_ID, napcat.whole_bans)
        self.assertTrue(all("权限不足" in _message_text(item["message"]) for item in napcat.group_messages))

    async def test_scheduler_recovers_after_bot_restart(self) -> None:
        self.app.close()
        first_app = GroupAdminApp(_config(self.root))
        first_napcat = MockNapCatServer()
        first_sender = first_napcat.connect(first_app)
        try:
            await first_napcat.send_event(first_app, _group_message([_text("/mute all 1s")]), first_sender)
            self.assertTrue(first_napcat.whole_bans[GROUP_ID])
            self.assertTrue(_scheduled_records(self.root))
        finally:
            first_napcat.disconnect(first_app, first_sender)
            first_app.close()

        second_app = GroupAdminApp(_config(self.root))
        second_napcat = MockNapCatServer(whole_bans={GROUP_ID: True})
        second_sender = second_napcat.connect(second_app)
        try:
            await asyncio.sleep(1.2)

            self.assertFalse(second_napcat.whole_bans[GROUP_ID])
            self.assertEqual(_actions(second_napcat), ["set_group_whole_ban"])
            self.assertEqual(_scheduled_records(self.root), [])
        finally:
            second_napcat.disconnect(second_app, second_sender)
            second_app.close()

    async def test_malformed_scheduled_records_are_ignored(self) -> None:
        _write_json(self.root / "scheduled_tasks.json", [{"group_id": GROUP_ID}, "broken"])
        self.app.close()
        self.app = GroupAdminApp(_config(self.root))
        napcat = MockNapCatServer()
        sender = napcat.connect(self.app)
        try:
            await asyncio.sleep(0.1)

            self.assertEqual(napcat.requests, [])
        finally:
            napcat.disconnect(self.app, sender)


class MockNapCatActionPermissionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = GroupAdminApp(_config(self.root, essence_permission=1))

    async def asyncTearDown(self) -> None:
        self.app.close()
        self.tmpdir.cleanup()

    async def test_kick_success(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/kick"), _at(MEMBER_ID)]))

        self.assertEqual(napcat.kicked_members, [{"group_id": GROUP_ID, "user_id": MEMBER_ID}])
        self.assertEqual(_audit_types(self.root), ["kick"])

    async def test_kick_bot_permission_denied(self) -> None:
        napcat = MockNapCatServer()
        napcat.set_failed_action("set_group_kick", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_text("/kick"), _at(MEMBER_ID)]))

        self.assertEqual(napcat.kicked_members, [])
        self.assertEqual(_actions(napcat), ["set_group_kick", "send_group_msg"])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_kick_operator_permission_denied(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            _group_message([_text("/kick"), _at(MEMBER_ID)], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertEqual(napcat.kicked_members, [])
        self.assertIn("权限不足", _last_group_text(napcat))

    async def test_recall_success(self) -> None:
        napcat = MockNapCatServer(messages={70001: _stored_message(70001, "需要撤回的消息")})

        await napcat.send_event(self.app, _group_message([_reply(70001), _text("/recall")]))

        self.assertEqual(_actions(napcat), ["get_msg", "delete_msg"])
        self.assertEqual(napcat.deleted_messages, [70001])
        self.assertEqual(_audit_types(self.root), ["recall"])

    async def test_recall_bot_permission_denied(self) -> None:
        napcat = MockNapCatServer(messages={70001: _stored_message(70001, "需要撤回的消息")})
        napcat.set_failed_action("delete_msg", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_reply(70001), _text("/recall")]))

        self.assertEqual(_actions(napcat), ["get_msg", "delete_msg", "send_group_msg"])
        self.assertEqual(napcat.deleted_messages, [])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_recall_operator_permission_denied(self) -> None:
        napcat = MockNapCatServer(messages={70001: _stored_message(70001, "需要撤回的消息")})

        await napcat.send_event(
            self.app,
            _group_message([_reply(70001), _text("/recall")], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertEqual(napcat.deleted_messages, [])
        self.assertIn("权限不足", _last_group_text(napcat))

    async def test_essence_success(self) -> None:
        napcat = MockNapCatServer(messages={70002: _stored_message(70002, "值得设精的消息")})

        await napcat.send_event(self.app, _group_message([_reply(70002), _text("/essence")]))

        self.assertEqual(_actions(napcat), ["get_msg", "set_essence_msg"])
        self.assertEqual(napcat.essence_messages, [70002])
        self.assertEqual(_audit_types(self.root), ["essence"])

    async def test_essence_bot_permission_denied(self) -> None:
        napcat = MockNapCatServer(messages={70002: _stored_message(70002, "值得设精的消息")})
        napcat.set_failed_action("set_essence_msg", retcode=1403, wording="权限不足")

        await napcat.send_event(self.app, _group_message([_reply(70002), _text("/essence")]))

        self.assertEqual(_actions(napcat), ["get_msg", "set_essence_msg", "send_group_msg"])
        self.assertEqual(napcat.essence_messages, [])
        self.assertIn("权限不足", _last_group_text(napcat))
        self.assertEqual(_audit_types(self.root), [])

    async def test_essence_operator_permission_denied(self) -> None:
        napcat = MockNapCatServer(messages={70002: _stored_message(70002, "值得设精的消息")})

        await napcat.send_event(
            self.app,
            _group_message([_reply(70002), _text("/essence")], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertEqual(napcat.essence_messages, [])
        self.assertIn("权限不足", _last_group_text(napcat))


class MockNapCatAuditHelpAndWebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.app = GroupAdminApp(_config(self.root))

    async def asyncTearDown(self) -> None:
        self.app.close()
        self.tmpdir.cleanup()

    async def test_audit_command_returns_recent_records(self) -> None:
        napcat = MockNapCatServer()
        await napcat.send_event(self.app, _group_message([_text("/mute"), _at(MEMBER_ID), _text("10s")]))

        await napcat.send_event(self.app, _group_message([_text("/audit")]))

        self.assertEqual(_actions(napcat), ["set_group_ban", "send_group_msg"])
        self.assertIn("当前群最近两周操作记录", _last_group_text(napcat))
        self.assertIn(f"禁言 {MEMBER_ID} 10秒", _last_group_text(napcat))
        record = _audit_records(self.root)[0]
        self.assertEqual(
            set(record),
            {
                "timestamp",
                "group_id",
                "operator_id",
                "operator_name",
                "message_id",
                "type",
                "target_id",
                "duration",
                "description",
            },
        )

    async def test_audit_log_rolls_old_records_out(self) -> None:
        old_record = {
            "timestamp": int(time.time()) - 15 * 24 * 60 * 60,
            "group_id": GROUP_ID,
            "operator_id": ADMIN_ID,
            "operator_name": "Old Admin",
            "message_id": 1,
            "type": "mute",
            "target_id": MEMBER_ID,
            "duration": 60,
            "description": "旧记录不应出现",
        }
        recent_record = {
            "timestamp": int(time.time()),
            "group_id": GROUP_ID,
            "operator_id": ADMIN_ID,
            "operator_name": "New Admin",
            "message_id": 2,
            "type": "mute",
            "target_id": MEMBER_ID,
            "duration": 60,
            "description": "新记录应该保留",
        }
        _write_audit_records(self.root, [old_record, recent_record])
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/audit")]))

        self.assertNotIn("旧记录不应出现", _last_group_text(napcat))
        self.assertIn("新记录应该保留", _last_group_text(napcat))
        self.assertEqual([record["description"] for record in _audit_records(self.root)], ["新记录应该保留"])

    async def test_malformed_audit_records_are_ignored(self) -> None:
        _write_json(self.root / "audit_log.json", {"not": "a list"})
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/audit")]))

        self.assertIn("当前群最近两周没有操作记录", _last_group_text(napcat))
        self.assertEqual(_audit_records(self.root), [])

    async def test_audit_operator_permission_denied(self) -> None:
        self.app.close()
        self.app = GroupAdminApp(_config(self.root, audit_permission=1))
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            _group_message([_text("/audit")], user_id=MEMBER_ID, role="member"),
        )

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertIn("权限不足", _last_group_text(napcat))

    async def test_help_command_replies(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(self.app, _group_message([_text("/gahelp")], user_id=MEMBER_ID, role="member"))

        self.assertEqual(_actions(napcat), ["send_group_msg"])
        self.assertIn("命令帮助", _last_group_text(napcat))
        self.assertIn("/mute", _last_group_text(napcat))

    async def test_websocket_connects_and_accepts_lifecycle_event(self) -> None:
        self.app.close()
        web_app = create_web_app(_config(self.root))
        client = TestClient(TestServer(web_app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/")
            await ws.send_str(
                _json(
                    {
                        "post_type": "meta_event",
                        "meta_event_type": "lifecycle",
                        "sub_type": "connect",
                        "self_id": 123456,
                    }
                )
            )

            self.assertFalse(ws.closed)
            await ws.close()
        finally:
            await client.close()

    async def test_heartbeat_packet_is_accepted_without_response(self) -> None:
        napcat = MockNapCatServer()

        await napcat.send_event(
            self.app,
            {
                "post_type": "meta_event",
                "meta_event_type": "heartbeat",
                "self_id": 123456,
                "status": {"online": True, "good": True},
                "interval": 5000,
            },
        )

        self.assertEqual(napcat.requests, [])
        self.assertEqual(napcat.responses, [])


def _config(
    root: Path,
    *,
    essence_permission: int = 0,
    audit_permission: int = 0,
) -> AppConfig:
    return AppConfig(
        group_whitelist={GROUP_ID},
        command_permissions={
            "recall": 1,
            "mute": 1,
            "unmute": 1,
            "kick": 1,
            "essence": essence_permission,
            "help": 0,
            "audit": audit_permission,
        },
        audit_log_file=root / "audit_log.json",
        scheduled_tasks_file=root / "scheduled_tasks.json",
        muted_members_file=root / "muted_members.json",
    )


def _group_message(
    message: list[dict[str, Any]],
    *,
    message_id: int = 80001,
    user_id: int = ADMIN_ID,
    role: str = "admin",
) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": GROUP_ID,
        "user_id": user_id,
        "message_id": message_id,
        "raw_message": _message_text(message),
        "sender": {"user_id": user_id, "role": role, "nickname": str(user_id)},
        "message": message,
    }


def _stored_message(message_id: int, text: str) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "message": [_text(text)],
        "raw_message": text,
    }


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": value}}


def _at(user_id: int) -> dict[str, Any]:
    return {"type": "at", "data": {"qq": str(user_id)}}


def _reply(message_id: int) -> dict[str, Any]:
    return {"type": "reply", "data": {"id": str(message_id)}}


def _message_text(message: list[dict[str, Any]]) -> str:
    text = []
    for segment in message:
        if segment.get("type") != "text":
            continue
        data = segment.get("data", {})
        if isinstance(data, dict):
            text.append(str(data.get("text", "")))
    return "".join(text)


def _last_group_text(napcat: MockNapCatServer) -> str:
    return _message_text(napcat.group_messages[-1]["message"])


def _actions(napcat: MockNapCatServer) -> list[str]:
    return [request["action"] for request in napcat.requests]


def _audit_records(root: Path) -> list[dict[str, Any]]:
    path = root / "audit_log.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)
    return records if isinstance(records, list) else []


def _write_audit_records(root: Path, records: list[dict[str, Any]]) -> None:
    _write_json(root / "audit_log.json", records)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)


def _audit_types(root: Path) -> list[str]:
    return [record["type"] for record in _audit_records(root)]


def _scheduled_records(root: Path) -> list[dict[str, Any]]:
    path = root / "scheduled_tasks.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)
    return records if isinstance(records, list) else []


def _muted_records(root: Path) -> list[dict[str, Any]]:
    path = root / "muted_members.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)
    return records if isinstance(records, list) else []


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)
