# groupadmin - 通过 OneBot V11 实现简单的群管理功能
# by littlekan233

'''
主模块 - 用python3运行我 求你了喵🥺
'''
from loguru import logger
from sys import stdout
from dotenv import load_dotenv
from os import environ
import logging, inspect, signal, asyncio, websockets, json

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

print(splash)
load_dotenv()
logger.debug("---ENV VAR START---")
for n, v in environ.items(): logger.debug(f"{n}={v}")
logger.debug("---ENV VAR END---")

# init logger
loglevel = environ.get("LOGLEVEL", "INFO").upper()
logger.remove()
logger.add(stdout, format = '[{time:HH:mm:ss}] [<c>{name}</c> | <lvl>{level}</lvl>] {message}', level=loglevel)
# 抄一下loguru文档的脚本（
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

logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
logger.success(f"已初始化 logger。日志等级：{loglevel}")

# setup ws server
import onebot, command # local module
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

# 这几行是为了兼容 fucking Windows
try:
    asyncio.run(main())
except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
    logger.debug("Windows fallback exit triggered")
    logger.info("[Windows fallback] 正在关闭服务器...")
logger.info("再见，期待您的下次使用。")