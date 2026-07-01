# groupadmin - 通过 OneBot V11 实现简单的群管理功能
# by littlekan233

'''
基础onebot连接处理喵
'''
from loguru import logger
import random, string

__all__ = [
    "process_event",
    "get_event",
    "build_request"
]

# 事件列表（key是id，value是原事件）
_events = {}
_pending_audits = {}
_audit_cmdtypes = {"recall", "mute", "unmute", "kick", "essence"}

def _add_event(evdata: dict) -> str:
    # 生成8位随机字符串（感谢DeepSeek）
    random_str = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
    if _events.get(random_str):
        return _add_event(evdata) # 有相同的了，再来！
    _events[random_str] = evdata
    return random_str

def process_event(event: dict) -> str | None:
    '''
    决定指定 OneBot 事件是否需要处理。

    传入的内容应为转化为 dict 的 OneBot V11 事件 JSON。
    
    返回 str 类型的 事件id 为需要进一步处理，
    返回 None 为不需要进一步处理。
    '''
    if event.get("post_type") == "meta_event" and event["meta_event_type"] == "lifecycle":
        # 生命周期事件，记录并不处理。
        qquin = event["self_id"]
        lctype = event["sub_type"]
        if lctype == "connect":
            logger.success(f"已连接到 OneBot {qquin}！")
            return
    elif event.get("post_type") == "message":
        # 这个真得处理了。
        # TODO: 真正不需要处理的事件也会在这里被加上需处理，感觉会降低效率...
        # TODO: 有空给这里优化一下
        eid = _add_event(event)
        logger.debug(f"收到消息事件（ID：{eid}），raw_message：{event["raw_message"]}") # 我直接上["raw_messsge"]，炸了我倒立
        return eid
    elif event.get("status") and "retcode" in event:
        # 这一大长串条件是为了确定这是个请求响应（
        eid = event["echo"] # 报错我倒立，我就不信有特殊的OneBot协议端
        # TODO: 感觉可能得套层 try-except 防止 eventid 出现不存在的意外情况
        # TODO: 但理论上来讲似乎不会出问题 谁知道实际会咋样呢（
        # 窝要验牌
        if event["status"] == "ok":
        # 牌没有问题
            pending_command = _pending_audits.pop(eid, None)
            if pending_command and eid in _events:
                from command import record_audit
                record_audit(_events[eid], pending_command)
            logger.success(f"ID 为 {eid} 的事件处理成功desuwa！")
        else:
            _pending_audits.pop(eid, None)
            logger.error(f"ID 为 {eid} 的事件处理失败！协议端返回：{event}")
        if eid: _events.pop(eid, None)
    return # 剩下的event基本上不用处理。

def get_event(eventid: str) -> dict:
    '''
    获取 ID 为 eventid 参数值 的事件。

    传入的内容为一个事件 ID。
    返回 dict 类型的事件信息。如果 dict 无任何内容则未查询到这个事件。
    '''
    return _events.get(eventid, {})

def build_request(command: dict) -> dict | None:
    '''
    构建一个发送给 OneBot 协议端的请求。

    传入的内容为一个 dict 类型的命令封装*。
    返回 dict 类型的 OneBot 请求，
    或者返回 None 表示不发送请求。

    *: 有关该封装的格式，详见command.py。
    '''
    eid = command["event_id"]
    if command.get("ignore", False):
        # 草，走！（发现ignore为true）忽略！
        logger.debug(f"ID 为 {eid} 的事件无需处理。")
        del _events[eid]
        return None
    if command["type"] in _audit_cmdtypes:
        _pending_audits[eid] = command
    # 要开始（构建 OneBot 请求）了哟～
    event = _events[eid]
    cmdtype = command["type"]
    if cmdtype == "error":
        return {
            "action": "send_group_msg", 
            "params": {
                "group_id": event["group_id"],
                "message": [
                    {
                        "type": "reply",
                        "data": {
                            "id": str(event["message_id"])
                        }
                    },
                    {
                        "type": "text",
                        "data": {
                            "text": command["errinfo"]
                        }
                    }
                ]
            },
            "echo": eid
        }
    elif cmdtype == "essence":
        return {
            "action": "set_essence_msg",
            "params": {
                "message_id": command["id"]
            },
            "echo": eid
        }
    elif cmdtype == "kick":
        return {
            "action": "set_group_kick",
            "params": {
                "group_id": str(event["group_id"]),
                "user_id": str(command["id"]),
            },
            "echo": eid
        }
    elif cmdtype == "mute":
        return {
            "action": "set_group_ban",
            "params": {
                "group_id": str(event["group_id"]),
                "user_id": str(command["id"]),
                "duration": command["time"]
            },
            "echo": eid
        }
    elif cmdtype == "unmute":
        return {
            "action": "set_group_ban",
            "params": {
                "group_id": str(event["group_id"]),
                "user_id": str(command["id"]),
                "duration": 0
            },
            "echo": eid
        }
    elif cmdtype == "recall":
        return {
            "action": "delete_msg",
            "params": {
                "message_id": command["id"]
            },
            "echo": eid
        }
