from __future__ import annotations

from aiohttp import WSMsgType, web
from loguru import logger

from groupadmin.app import GroupAdminApp
from groupadmin.config import AppConfig


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    bot: GroupAdminApp = request.app["bot"]
    async def send_request(payload: str) -> None:
        await ws.send_str(payload)

    bot.attach_request_sender(send_request)
    logger.debug("有新连接加入！")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                response = await bot.handle_text(msg.data, send_request)
                if response:
                    logger.debug(f"will send: {response}")
                    await send_request(response)
            elif msg.type == WSMsgType.ERROR:
                logger.warning(f"WebSocket 连接异常关闭：{ws.exception()}")
    finally:
        bot.detach_request_sender(send_request)

    logger.info("WebSocket 连接已关闭。")
    return ws


def create_web_app(config: AppConfig) -> web.Application:
    app = web.Application()
    app["bot"] = GroupAdminApp(config)
    app.router.add_get("/{tail:.*}", websocket_handler)
    app.on_cleanup.append(cleanup_app)
    return app


async def cleanup_app(app: web.Application) -> None:
    bot: GroupAdminApp = app["bot"]
    bot.close()


def run_server(config: AppConfig) -> None:
    logger.success(f"反向 WebSocket 正在 {config.ws_host}:{config.ws_port} 上监听！")
    logger.success(
        "请去协议端添加 WebSocket 客户端（或反向WebSocket之类的字眼），"
        f"地址填写：ws://{config.ws_host}:{config.ws_port}/"
    )
    try:
        web.run_app(
            create_web_app(config),
            host=config.ws_host,
            port=config.ws_port,
            print=None,
            handle_signals=True,
        )
    finally:
        logger.info("再见，期待您的下次使用。")
