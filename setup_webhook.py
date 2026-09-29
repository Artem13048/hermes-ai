"""
Разовый скрипт регистрации webhook-подписки в MAX Bot API.

Читает MAX_BOT_TOKEN, MAX_WEBHOOK_SECRET и WEBHOOK_URL из .env.

Запуск:
    python setup_webhook.py
"""

import json
import os
import ssl
import urllib.error
import urllib.request

from dotenv import load_dotenv


# ============================================================
# ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================================

load_dotenv()

TOKEN = os.getenv("MAX_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("MAX_WEBHOOK_SECRET")

# Проверяем, что всё есть
if not TOKEN:
    raise RuntimeError("MAX_BOT_TOKEN не найден в .env")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL не найден в .env")

if not WEBHOOK_SECRET:
    raise RuntimeError("MAX_WEBHOOK_SECRET не найден в .env")

# Проверка длины секрета (требование MAX: 5–256 символов)
if not (5 <= len(WEBHOOK_SECRET) <= 256):
    raise RuntimeError(
        "MAX_WEBHOOK_SECRET должен быть 5–256 символов"
    )


# ============================================================
# РЕГИСТРАЦИЯ WEBHOOK
# ============================================================

payload = {
    "url": WEBHOOK_URL,
    "update_types": [
        "message_created",
        "message_callback",
        "bot_started",
    ],
    "secret": WEBHOOK_SECRET,
}

# SSL-контекст с бандлом сертификатов Минцифры.
# Файл certificates.pem должен лежать рядом со скриптом.
ssl_context = ssl.create_default_context()

CA_BUNDLE = os.getenv("MAX_CA_BUNDLE", "certificates.pem")

if not os.path.exists(CA_BUNDLE):
    raise RuntimeError(
        f"Не найден файл сертификатов: {CA_BUNDLE}. "
        f"Скачайте корневой и промежуточный сертификаты Минцифры "
        f"и объедините их в {CA_BUNDLE}."
    )

ssl_context.load_verify_locations(CA_BUNDLE)


req = urllib.request.Request(
    "https://platform-api2.max.ru/subscriptions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": TOKEN,
        "Content-Type": "application/json",
    },
    method="POST",
)


try:
    with urllib.request.urlopen(
        req, timeout=30, context=ssl_context
    ) as resp:
        body = resp.read().decode("utf-8")
        print("HTTP", resp.status)
        print(body)
        print()
        print("Webhook успешно зарегистрирован.")
        print(f"URL: {WEBHOOK_URL}")
        print(f"События: {payload['update_types']}")

except urllib.error.HTTPError as e:
    print("HTTP ERROR", e.code)
    print(e.read().decode("utf-8"))

except Exception as e:
    print("ERROR:", e)