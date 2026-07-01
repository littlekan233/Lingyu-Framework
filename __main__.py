# groupadmin - 通过 OneBot V11 实现简单的群管理功能
# by littlekan233

'''
主模块 - 用python3运行我 求你了喵🥺
'''
from loguru import logger
from sys import stdout
from dotenv import load_dotenv
from os import environ
from pathlib import Path
import logging, inspect, signal, asyncio, websockets, json, subprocess, sys, time

_reload_child_env = "GROUPADMIN_RELOAD_CHILD"
_reload_switch_env = "GROUPADMIN_HOT_RELOAD"
_reload_interval_env = "GROUPADMIN_RELOAD_INTERVAL"
_reload_watch_names = {".env", "requirements.txt"}
_reload_watch_suffixes = {".py"}
_reload_ignore_dirs = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

splash = r'''
  ______                                         _         _        
 / _____)                              /\       | |       (_)       
| /  ___   ____  ___   _   _  ____    /  \    _ | | ____   _  ____  
| | (___) / ___)/ _ \ | | | ||  _ \  / /\ \  / || ||    \ | ||  _ \ 
| \____/|| |   | |_| || |_| || | | || |__| |( (_| || | | || || | | |
 \_____/ |_|    \___/  \____|| ||_/ |______| \____||_|_|_||_||_| |_|
                             |_|                                    

groupadmin by littlekan233
为 QQ Bot 提供简单的群管理功能。
'''

# 抄一下loguru文档的代码（
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logger(show_startup_log: bool = True) -> None:
    # init logger
    loglevel = (environ.get("LOGLEVEL") or environ.get("LOG_LEVEL") or "INFO").upper()
    logger.remove()
    logger.add(stdout, format = '[{time:HH:mm:ss}] [<c>{name}</c> | <lvl>{level}</lvl>] {message}', level=loglevel)
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.debug("---ENV VAR START---")
    for n, v in environ.items(): logger.debug(f"{n}={v}")
    logger.debug("---ENV VAR END---")
    if show_startup_log:
        logger.success(f"已初始化 logger。日志等级：{loglevel}")

# setup ws server
onebot = None
command = None

def _load_local_modules():
    global onebot, command
    if onebot and command:
        return
    import onebot as _onebot
    import command as _command
    onebot = _onebot
    command = _command

@logger.catch(exclude=(KeyboardInterrupt, EOFError, asyncio.CancelledError))
async def ws_server_process(ws: websockets.ServerConnection):
    logger.debug("有新连接加入！")
    try:
        while True:
            try:
                msg = await ws.recv()
                # 防止这里写一大串于是把onebot处理做成了模块（
                eventid = onebot.process_event(json.loads(msg))
                if eventid:
                    cmd = command.process_msg(eventid)
                    request = onebot.build_request(cmd)
                    if request: 
                        sendjson = json.dumps(request)
                        logger.debug(f"type(request): {type(request)}, request: {request} , (will send) dumped: {sendjson}")
                        await ws.send(sendjson)
            except websockets.exceptions.ConnectionClosed as cci:
                # cci: Connection Close Info（连接关闭信息）
                if type(cci) != websockets.ConnectionClosedOK: 
                    logger.warning(f"WebSocket 连接异常关闭。代码：{cci.code}，原因：{cci.reason}")
                else:
                    logger.info("WebSocket 连接正常关闭。")
                break
    except asyncio.CancelledError:
        logger.debug("收到 asyncio.CancelledError")

# 给慕斯大头！
async def main():
    _load_local_modules()
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    try:
        loop.add_signal_handler(signal.SIGINT, future.set_result, None)
        loop.add_signal_handler(signal.SIGTERM, future.set_result, None)
    except NotImplementedError:
        # only on fucking Windows
        # fuck u microsoft
        pass

    host = environ.get("WSR_HOST", "127.0.0.1")
    port = int(environ.get("WSR_PORT", "20721"))
    async with websockets.serve(ws_server_process, host, port) as server:
        logger.success(f"反向 WebSocket 正在 {host}:{port} 上监听！")
        logger.success(f"请去协议端添加 WebSocket 客户端（或反向WebSocket之类的字眼），地址填写：ws://{host}:{port}/")
        await future

def _env_switch_enabled(name: str) -> bool:
    return environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

def _reload_enabled() -> bool:
    if environ.get(_reload_child_env) == "1":
        return False
    return "--reload" in sys.argv or _env_switch_enabled(_reload_switch_env)

def _iter_reload_watch_files():
    root = Path(__file__).resolve().parent
    for path in root.rglob("*"):
        if any(part in _reload_ignore_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in _reload_watch_suffixes or path.name in _reload_watch_names:
            yield path

def _reload_snapshot() -> dict[Path, int]:
    snapshot = {}
    for path in _iter_reload_watch_files():
        try:
            snapshot[path] = path.stat().st_mtime_ns
        except OSError:
            pass
    return snapshot

def _find_reload_change(old: dict[Path, int], new: dict[Path, int]) -> Path | None:
    for path in sorted(set(old) ^ set(new)):
        return path
    for path in sorted(set(old) & set(new)):
        if old[path] != new[path]:
            return path
    return None

def _reload_child_args() -> list[str]:
    script = str(Path(__file__).resolve())
    args = [arg for arg in sys.argv[1:] if arg != "--reload"]
    return [sys.executable, script, *args]

def _start_reload_child() -> subprocess.Popen:
    child_env = environ.copy()
    child_env[_reload_child_env] = "1"
    child_env[_reload_switch_env] = "0"
    return subprocess.Popen(_reload_child_args(), env=child_env)

def _stop_reload_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("热重载子进程未及时退出，强制结束。")
        process.kill()
        process.wait()

def run_with_reloader() -> None:
    interval = float(environ.get(_reload_interval_env, "1"))
    logger.success(f"热重载已启用，正在监视 Python 文件、.env 和 requirements.txt。")
    logger.info("修改文件后会自动重启 Bot；按 Ctrl+C 退出热重载。")
    snapshot = _reload_snapshot()
    process = _start_reload_child()
    try:
        while True:
            time.sleep(interval)
            new_snapshot = _reload_snapshot()
            changed = _find_reload_change(snapshot, new_snapshot)
            if not changed:
                continue

            logger.info(f"检测到文件变化：{changed.relative_to(Path(__file__).resolve().parent)}，正在重启...")
            _stop_reload_child(process)
            snapshot = new_snapshot
            process = _start_reload_child()
    except KeyboardInterrupt:
        logger.info("正在关闭热重载...")
    finally:
        _stop_reload_child(process)

def run_server() -> None:
    # 这几行是为了兼容 fucking Windows
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        logger.debug("Windows fallback exit triggered")
        logger.info("[Windows fallback] 正在关闭服务器...")
    logger.info("再见，期待您的下次使用。")

if __name__ == "__main__":
    is_reload_child = environ.get(_reload_child_env) == "1"
    load_dotenv(override=is_reload_child)
    if is_reload_child:
        environ[_reload_child_env] = "1"
    if _reload_enabled():
        setup_logger(show_startup_log=False)
        run_with_reloader()
    else:
        print(splash)
        setup_logger()
        run_server()
