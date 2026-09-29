"""
Сервис для работы с временем работы мест.
"""

import logging
import re
from datetime import datetime, time



# ПАРСИНГ ВРЕМЕНИ РАБОТЫ

def parse_opening_hours(opening_hours: str | None) -> tuple[time, time] | str | None:
    """
    Парсит строку времени работы.

    Возвращает:
    - "all_day" если «Круглосуточно»
    - (start_time, end_time) если «08:00-15:00»
    - None если неизвестно
    """
    if not opening_hours:
        return None

    opening_hours = opening_hours.strip().lower()

    # Круглосуточно
    if "круглосуточно" in opening_hours or "24/7" in opening_hours:
        return "all_day"

    # Формат 08:00-15:00
    match = re.search(
        r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})",
        opening_hours,
    )
    if match:
        start_h, start_m = int(match.group(1)), int(match.group(2))
        end_h, end_m = int(match.group(3)), int(match.group(4))

        try:
            start = time(start_h, start_m)
            end = time(end_h, end_m)
            return start, end
        except ValueError:
            return None

    return None


# ПРОВЕРКА — ОТКРЫТО ЛИ СЕЙЧАС

def is_place_open(opening_hours: str | None, now: datetime | None = None) -> bool:
    """
    Проверяет, открыто ли место сейчас.

    Если opening_hours = None — считаем, что открыто (неизвестно).
    """
    if now is None:
        now = datetime.now()

    parsed = parse_opening_hours(opening_hours)

    # Неизвестно — считаем открытым
    if parsed is None:
        return True

    # Круглосуточно
    if parsed == "all_day":
        return True

    # Временной интервал
    start, end = parsed
    current_time = now.time()

    # Если интервал переходит через полночь (22:00-06:00)
    if start > end:
        # Работает либо с 22:00 до 23:59, либо с 00:00 до 06:00
        return current_time >= start or current_time <= end

    # Обычный интервал
    return start <= current_time <= end


# ФИЛЬТРАЦИЯ МЕСТ ПО ВРЕМЕНИ РАБОТЫ

def filter_places_by_schedule(places: list) -> tuple[list, str | None]:
    """
    Убирает закрытые места.
    Возвращает (отфильтрованные места, причина).
    """
    now = datetime.now()

    opened = []
    closed_count = 0

    for place in places:
        opening_hours = getattr(place, "opening_hours", None)
        if is_place_open(opening_hours, now):
            opened.append(place)
        else:
            closed_count += 1

    # Если все закрыты — не фильтруем
    if not opened:
        return places, None

    reason = None
    if closed_count > 0:
        reason = (
            f"⏰ Сейчас {now.strftime('%H:%M')} — "
            f"{closed_count} мест закрыто, показаны только открытые"
        )

    return opened, reason