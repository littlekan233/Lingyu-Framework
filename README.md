# Lingyu-Framework
从头开始写的一个Bot框架，基于Python，通过OneBot V11通信。

> [!WARNING]
> 代码质量低下注意⚠️

## 开发模式热重载

启动时加上 `--reload` 可以开启热重载：

```bash
python3 __main__.py --reload
```

也可以通过环境变量开启：

```bash
GROUPADMIN_HOT_RELOAD=1 python3 __main__.py
```

热重载会监视项目里的 Python 文件、`.env` 和 `requirements.txt`。检测到变化后，会自动重启 Bot 子进程。

<!--这里还没写完-->
