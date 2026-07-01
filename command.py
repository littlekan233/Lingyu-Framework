# groupadmin - 通过 OneBot V11 实现简单的群管理功能
# by littlekan233

'''
命令处理喵
会返回一个较为统一的dict对象：
{
    "event_id": str, # 事件 ID，必填
    "ignore": bool, # 加上之后指定的请求会被忽略，剩下的内容一概不看不处理，可选（默认false）
    "type": "recall" | "mute" | "unmute" | "kick" | "essence" | "error", # 分别代表撤回、禁言、取消禁言、踢人、设为精华，最后一个是命令发生错误，必填
    "id": int, # 当 type 为 mute 或 kick 时，该值为 QQ 号；当 type 为 recall 或 kksk 时，该值为 OneBot V11 的消息 ID；当 type 为 error 时，该值无效
    "errinfo": str, # 错误信息，仅 type 为 error 时必填
    "time": int # 禁言时间（秒），仅 type 为 mute 时必填
}
'''

from onebot import get_event
from loguru import logger
from os import environ
from pathlib import Path
import json, re, time

__all__ = [
    "process_msg",
    "record_audit"
]

# 白名单
whitelist = eval(environ.get("GROUP_WHITELIST", "[]"))

# 权限机制 - 命令
_perm_cmd = {
    "do_recall": int(environ.get("PERM_RECALL", "1")),
    "do_mute": int(environ.get("PERM_MUTE", "1")),
    "do_unmute": int(environ.get("PERM_MUTE", "1")),
    "do_kick": int(environ.get("PERM_KICK", "1")),
    "do_essence": int(environ.get("PERM_ESSENCE", "0")),
    "do_help": int(environ.get("PERM_HELP", "0")),
    "do_audit": int(environ.get("PERM_AUDIT", environ.get("PERM_HELP", "0")))
}

_audit_log_path = Path(environ.get("AUDIT_LOG_FILE", "audit_log.json"))
_audit_retention_secs = 14 * 24 * 60 * 60

# 权限机制 - 成员权限等级覆写
_perm_member_override = {}
for _item in eval(environ.get("MEMBER_PERM_OVERRIDE", "[]")):
    # 遍历.env中的MEMBER_PERM_OVERRIDE，解读信息然后存到_perm_member_override中
    item = _item.split(":")
    if len(item) != 3:
        logger.warning(f"成员权限等级覆写项“{_item}”不合法，该项无效。")
        continue
    if not _perm_member_override.get(str(item[0])):
        # 这个群没在覆写词典中
        _perm_member_override[str(item[0])] = {}
    _perm_member_override[str(item[0])][str(item[1])] = int(item[2])

def _get_user_permlevel(group_id: int, sender: dict) -> int:
    '''
    获取用户权限等级。
    先从权限等级覆写开始看，然后从sender中成员的role看。

    传入两个参数，第一个为群号，第二个为OneBot V11 定义的消息事件 sender 字段定义。
    返回以下权限等级（类型为 int）：
    0 - 普通成员
    1 - 管理员
    2 - 群主
    '''
    try:
        return _perm_member_override[str(group_id)][str(sender["user_id"])]
    except KeyError:
        role = sender["role"]
        if role == "owner":
            return 2
        elif role == "admin":
            return 1
        elif role == "member":
            return 0

def _parse_time(time: str) -> int:
    '''
    将按格式的字符串时间转化为以秒为单位的时间。
    支持天、小时、分钟、秒。

    传入的内容是 str 类型的带格式的时间。
    返回一个 int 类型的以秒为单位的时间。
    '''
    time = time.strip().replace(" ", "")
    time = time.replace('天', 'd').replace("day","d").replace('小时', 'h').replace("hour","h")\
    .replace('分钟', 'm').replace('分', 'm').replace('min','m').replace('sec','s').replace('秒', 's')
    if not time or not re.fullmatch(r'(\d+[dhms]?)+', time):
        raise ValueError("你确定你传进来的参数合法吗？")
    timesecs = 0
    pattern = re.findall(r"(\d+)([dhms]?)", time)
    for val, unit in pattern:
        val = int(val)
        if unit == "d": timesecs += val * 86400
        elif unit == "h": timesecs += val * 3600
        elif unit == "m": timesecs += val * 60
        elif unit == "s" or unit == "": timesecs += val
    return timesecs

def _load_audit_records() -> list[dict]:
    try:
        with _audit_log_path.open("r", encoding="utf-8") as f:
            records = json.load(f)
        if isinstance(records, list):
            return records
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取操作记录失败：{e}")
    return []

def _save_audit_records(records: list[dict]) -> None:
    try:
        with _audit_log_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"保存操作记录失败：{e}")

def _prune_audit_records(records: list[dict], now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    min_ts = now - _audit_retention_secs
    return [record for record in records if int(record.get("timestamp", 0)) >= min_ts]

def _format_time(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0秒"
    parts = []
    for unit, unit_seconds in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
        value, seconds = divmod(seconds, unit_seconds)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)

def _describe_command(command: dict) -> str:
    cmdtype = command["type"]
    if cmdtype == "recall":
        return f"撤回消息 {command['id']}"
    if cmdtype == "essence":
        return f"设置精华消息 {command['id']}"
    if cmdtype == "mute":
        return f"禁言 {command['id']} {_format_duration(command['time'])}"
    if cmdtype == "unmute":
        return f"解除禁言 {command['id']}"
    if cmdtype == "kick":
        return f"踢出 {command['id']}"
    return cmdtype

def record_audit(event: dict, command: dict) -> None:
    records = _prune_audit_records(_load_audit_records())
    sender = event["sender"]
    records.append({
        "timestamp": int(time.time()),
        "group_id": int(event["group_id"]),
        "operator_id": int(sender["user_id"]),
        "operator_name": sender.get("card") or sender.get("nickname") or str(sender["user_id"]),
        "message_id": int(event["message_id"]),
        "type": command["type"],
        "target_id": command.get("id"),
        "duration": command.get("time"),
        "description": _describe_command(command)
    })
    _save_audit_records(records)

def _build_audit_message(group_id: int) -> str:
    records = _prune_audit_records(_load_audit_records())
    _save_audit_records(records)
    group_records = [record for record in records if int(record.get("group_id", 0)) == int(group_id)]
    if not group_records:
        return "当前群最近两周没有操作记录。"
    lines = ["当前群最近两周操作记录："]
    for record in group_records:
        lines.append(
            f"{_format_time(int(record['timestamp']))} "
            f"{record.get('operator_name', record.get('operator_id'))}({record.get('operator_id')}) "
            f"{record.get('description', record.get('type'))}"
        )
    return "\n".join(lines)

# 通用命令封装builder（ignore, error）
_ignore = lambda e: { "event_id": e, "ignore": True }
_error = lambda e, r: { "event_id": e, "type": "error", "errinfo": r }

# 各个命令的handler
def do_recall(eventid: str, target_msg: int, **kwargs) -> dict:
    '''
    撤回命令的handler。
    '''
    if target_msg <= 0:
        return _error(eventid, "引用的消息无效。")
    return { "event_id": eventid, "type": "recall", "id": target_msg }
    # 所以你写这个handler就为了这么一点return？

def do_essence(eventid: str, target_msg: int, **kwargs) -> dict:
    if target_msg <= 0:
        return _error(eventid, "引用的消息无效。")
    return { "event_id": eventid, "type": "essence", "id": target_msg }

def do_mute(eventid: str, target_qq: int, mute_time: str, **kwargs) -> dict:
    if target_qq <= 0: 
        return _error(eventid, "目标用户不存在。")
    try:
        return { "event_id": eventid, "type": "mute", "id": target_qq, "time": _parse_time(mute_time) }
    except ValueError:
        return _error(eventid, "无效的时间格式。")

def do_unmute(eventid: str, target_qq: int, **kwargs):
    if target_qq <= 0:
        return _error(eventid, "目标用户不存在。")
    return { "event_id": eventid, "type": "unmute", "id": target_qq }

def do_kick(eventid: str, target_qq: int, **kwargs):
    if target_qq <= 0:
        return _error(eventid, "目标用户不存在。")
    return { "event_id": eventid, "type": "kick", "id": target_qq }

def do_help(eventid: str, target_msg: int, **kwargs) -> dict:
    '''
    帮助命令的handler。

    神人阚大题小做.jpg
    '''
    # 这里是帮助信息，不能走缩进（
    helpmsg = f"""[GroupAdmin] by littlekan233
https://github.com/littlekan233/groupadmin

命令帮助：
撤回：引用一条消息并发送 /recall 或者 /撤回
设为精华：引用一条消息并发送 /essence 或者 /设精
禁言：发送 /mute @xxx 时间 或者 /禁言 @xxx 时间
解禁：发送 /unmute @xxx 或者 /解禁 @xxx
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
    return {
        "event_id": eventid,
        "type": "error",
        "errinfo": helpmsg
    } # 谁他妈教你帮助用error的？？？

def do_audit(eventid: str, group_id: int, **kwargs) -> dict:
    return {
        "event_id": eventid,
        "type": "error",
        "errinfo": _build_audit_message(group_id)
    }

# placeholder handler
def placeholder_handler(**kwargs):
    pass

# 命令处理
def process_msg(eventid: str) -> dict:
    '''
    获取并处理消息。

    传入参数为事件 ID。
    返回一个 dict 类型的命令封装*。

    *: 有关该封装的格式，详见command.py。
    '''
    event = get_event(eventid)
    if event == {}:
        logger.error("在处理命令时遇到了一个滚木事件！")
        return _ignore(eventid)
    if event["message_type"] != "group":
        logger.debug("非群聊消息，pass")
        return _ignore(eventid)
    if event["group_id"] not in whitelist:
        logger.debug("未在白名单，pass")
        return _ignore(eventid)
    
    # 找找text/at/reply segment并提取出有用信息
    logger.debug(f"processing message, message_id: {event["message_id"]}")
    target_msgid = target_qquid = 0
    cmd_handler = placeholder_handler # 这里是一个函数
    mute_time = ""
    for segment in event["message"]:
        segtype = segment["type"]
        if segtype == "reply":
            target_msgid = int(segment["data"]["id"])
            logger.info(f"found reply, target message_id: {segment["data"]["id"]}")
        elif segtype == "at":
            logger.info(f"found at, target user_id: {segment["data"]["qq"]}")
            if cmd_handler:
                target_qquid = int(segment["data"]["qq"])
        elif segtype == "text":
            # 这里是重头戏
            msg = segment["data"]["text"].strip()
            if msg == "/recall" or msg == "/撤回":
                cmd_handler = do_recall
            elif msg == "/essence" or msg == "/设精":
                cmd_handler = do_essence
            elif msg == "/kick" or msg == "/踢人":
                cmd_handler = do_kick
            elif msg == "/mute" or msg == "/禁言":
                cmd_handler = do_mute
            elif msg == "/unmute" or msg == "/解禁":
                cmd_handler = do_unmute
            elif msg == "/gahelp" or msg == "/帮助":
                cmd_handler = do_help
            elif msg == "/audit" or msg == "/操作记录":
                cmd_handler = do_audit
            else: # 这一块是为了/mute @xxx <time>的time能被解析到。
                if cmd_handler == do_mute and target_qquid > 0:
                    # ok确认这一段消息是禁言时间
                    mute_time = msg
    
    if cmd_handler.__name__ != "placeholder_handler":
        logger.debug(f"will run handler {cmd_handler.__name__}, permlevel at least {_perm_cmd[cmd_handler.__name__]}")
    senderperm = _get_user_permlevel(event["group_id"], event["sender"])
    if cmd_handler.__name__ != "placeholder_handler" and senderperm >= _perm_cmd[cmd_handler.__name__]: 
        # 用户权限检查通过
        command = cmd_handler(eventid=eventid, group_id=event["group_id"], target_msg=target_msgid, target_qq=target_qquid, mute_time=mute_time)
        return command
    elif cmd_handler.__name__ != "placeholder_handler":
        logger.debug(f"perm not pass. sender perm: {senderperm}")
    return _ignore(eventid)
