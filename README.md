# Lingyu-Framework · 零羽框架

简单的基于 OneBot v11 协议的 QQ 群管机器人框架。Made with ♥️ and Codex.

## 使用

零羽框架采用 uv 自动管理依赖和虚拟环境。

在协议端配置好标有「WebSocket 客户端」或「反向 WebSocket」字样的连接，指向 `.env` 文件中设置的端口，
并在 `.env` 中配置启用群列表、权限覆盖、权限等级等选项后，使用如下命令启动零羽：

```bash
$ uv run .
```

在配置好的列表中的群内使用 `/gahelp` 或 `/帮助` 来看看零羽酱能做什么吧！

## 功能

- [x] 撤回消息
- [x] 禁言
  - [x] 禁言防绕过
  - [x] 计时的全体禁言
- [x] 设精  
  > 好烦啊为啥群成员不能设精啊 ——岩
- [x] 操作审计
  - [ ] 自定义审计滚动窗口长度
- [ ] 入群申请自动审核
- [ ] 更多鬼点子正在生成中……

## 开发

本项目结构如下：

```
.
├── __main__.py                 # 程序入口，加载配置、初始化日志并启动服务
├── groupadmin/                 # 零羽框架核心代码
│   ├── app.py                  # 事件处理主流程，串联指令、OneBot 请求、审计和调度
│   ├── audit.py                # 群管操作审计记录与查询消息构建
│   ├── commands.py             # 群管指令解析、权限校验和动作分发
│   ├── config.py               # 环境变量配置加载与校验
│   ├── logging.py              # loguru 日志初始化和标准 logging 接管
│   ├── models.py               # OneBot 事件、动作和持久化记录的数据模型
│   ├── mute_records.py         # 成员禁言记录持久化，用于退群重进后恢复禁言
│   ├── onebot.py               # OneBot v11 API 请求构建适配层
│   ├── reloader.py             # 开发热重载监视与子进程管理
│   ├── scheduler.py            # 全体禁言自动解除任务调度与持久化
│   ├── server.py               # aiohttp 反向 WebSocket 服务
│   └── store.py                # 内存事件状态与 OneBot echo 关联
├── tests/                      # unittest 测试目录
│   ├── support/
│   │   └── mock_napcat.py      # 集成测试用 OneBot/NapCat 协议模拟器
│   └── test_groupadmin.py      # 核心功能测试
├── .env.example                # 环境变量示例
├── requirements.txt            # pip 依赖清单
├── pyproject.toml              # uv / Python 项目元数据
├── uv.lock                     # uv lockfile
```

零羽框架支持热重载，添加 `--reload` 或设置 `GROUPADMIN_HOT_RELOAD=1` 可启用热重载功能，在有文件更改时自动重启框架便于调试：

```bash
$ uv run . --reload
```

欢迎向本项目提交新功能！

## 鸣谢

感谢山姆奥特曼在不知情情况下提供的 300 个 OpenAI Free 账号支持了本项目开发！

## 开源许可 / LICENSE

Copyright (c) 2026 小阚LittleKan  
SPDX-License-Identifier: MIT