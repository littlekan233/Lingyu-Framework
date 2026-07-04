from __future__ import annotations

from os import environ

from dotenv import load_dotenv

from groupadmin.config import load_config
from groupadmin.logging import setup_logger
from groupadmin.reloader import reload_enabled, run_with_reloader
from groupadmin.server import run_server


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


def main() -> None:
    is_reload_child = environ.get("GROUPADMIN_RELOAD_CHILD") == "1"
    load_dotenv(override=is_reload_child)

    if reload_enabled():
        setup_logger(show_startup_log=False)
        run_with_reloader()
        return

    print(splash)
    setup_logger()
    config = load_config()
    run_server(config)


if __name__ == "__main__":
    main()
