"""
Сервис для получения текущей погоды через OpenWeatherMap.
"""

import logging
import os

import requests
from dotenv import load_dotenv


load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def get_weather(latitude: float, longitude: float) -> dict:
    """
    Получает текущую погоду по координатам.

    Возвращает:
    {
        "main": "rain" | "snow" | "clear" | "clouds" | "unknown",
        "description": "лёгкий дождь",
        "temp": 15,           # ОКРУГЛЁННОЕ целое
        "is_bad": True / False,
        "icon": "🌧",
    }
    """
    default = {
        "main": "unknown",
        "description": "",
        "temp": None,
        "is_bad": False,
        "icon": "🌍",
    }

    if not OPENWEATHER_API_KEY:
        logging.warning("OPENWEATHER_API_KEY не задан")
        return default

    try:
        params = {
            "lat": latitude,
            "lon": longitude,
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "ru",
        }

        response = requests.get(WEATHER_URL, params=params, timeout=10)
        response.raise_for_status()

        # ⚠️ ВАЖНО: получаем данные из ответа
        data = response.json()

        # Погода
        main = data["weather"][0]["main"].lower()
        description = data["weather"][0]["description"]
        temp = data["main"]["temp"]

        # Плохая ли погода для прогулок
        is_bad = main in ("rain", "snow", "thunderstorm", "drizzle")

        # Иконка
        icon = {
            "rain": "🌧",
            "drizzle": "🌦",
            "snow": "❄️",
            "thunderstorm": "⛈",
            "clear": "☀️",
            "clouds": "☁️",
            "mist": "🌫",
            "fog": "🌫",
        }.get(main, "🌍")

        result = {
            "main": main,
            "description": description,
            # ⚠️ ОКРУГЛЯЕМ до целого
            "temp": int(round(temp)) if temp is not None else None,
            "is_bad": is_bad,
            "icon": icon,
        }

        logging.info(f"Погода: {result}")
        return result

    except Exception:
        logging.exception("Ошибка получения погоды")
        return default


# ============================================================
# ФИЛЬТРАЦИЯ МЕСТ ПО ПОГОДЕ
# ============================================================

def filter_places_by_weather(
    places: list,
    weather: dict,
) -> tuple[list, str | None]:
    """
    Убирает парки, если погода плохая.

    Возвращает (отфильтрованные места, причина).
    """
    if not weather.get("is_bad"):
        return places, None

    icon = weather.get("icon", "")
    desc = weather.get("description", "")

    # При дожде / снеге — убираем парки (category_id = 3)
    filtered = [
        p for p in places
        if getattr(p, "category_id", None) != 3
    ]

    # Если после фильтра ничего не осталось — возвращаем исходные
    if not filtered:
        return places, None

    reason = (
        f"{icon} {desc.capitalize()} — "
        f"парки исключены из маршрута"
    )

    return filtered, reason