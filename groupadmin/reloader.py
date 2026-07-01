from __future__ import annotations

import subprocess
import sys
import time
from os import environ
from pathlib import Path

from loguru import logger


_reload_child_env = "GROUPADMIN_RELOAD_CHILD"
_reload_switch_env = "GROUPADMIN_HOT_RELOAD"
_reload_interval_env = "GROUPADMIN_RELOAD_INTERVAL"
_reload_watch_names = {".env", "requirements.txt"}
_reload_watch_suffixes = {".py"}
_reload_ignore_dirs = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def reload_enabled() -> bool:
    if environ.get(_reload_child_env) == "1":
        return False
    return "--reload" in sys.argv or _env_switch_enabled(_reload_switch_env)


def run_with_reloader() -> None:
    interval = float(environ.get(_reload_interval_env, "1"))
    logger.success("热重载已启用，正在监视 Python 文件、.env 和 requirements.txt。")
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

            logger.info(f"检测到文件变化：{changed.relative_to(_project_root())}，正在重启...")
            _stop_reload_child(process)
            snapshot = new_snapshot
            process = _start_reload_child()
    except KeyboardInterrupt:
        logger.info("正在关闭热重载...")
    finally:
        _stop_reload_child(process)


def _env_switch_enabled(name: str) -> bool:
    return environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _iter_reload_watch_files():
    root = _project_root()
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
    script = str(_project_root() / "__main__.py")
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
