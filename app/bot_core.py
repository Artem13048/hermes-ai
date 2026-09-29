"""
Ядро бота
"""

import logging
import os

from dotenv import load_dotenv
from maxapi import Bot, Dispatcher

try:
    from maxapi.context.isolation import SimpleEventIsolation
    _HAS_ISOLATION = True
except ImportError:
    _HAS_ISOLATION = False

load_dotenv()

MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
if not MAX_BOT_TOKEN:
    raise RuntimeError("Не найден MAX_BOT_TOKEN в файле .env")

bot = Bot(token=MAX_BOT_TOKEN)

if _HAS_ISOLATION:
    dp = Dispatcher(isolation=SimpleEventIsolation())
    logging.info("SimpleEventIsolation подключён")
else:
    dp = Dispatcher()
    logging.warning("SimpleEventIsolation недоступен — без изоляции")