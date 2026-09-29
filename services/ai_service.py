"""
Сервис работы с GigaChat для парсинга запросов пользователя.
С фолбэком на регулярные выражения — если GigaChat не понял.
"""

import json
import logging
import os
import re

from dotenv import load_dotenv
from gigachat import GigaChat


load_dotenv()

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")


# ============================================================
# ПРОМПТ
# ============================================================

SYSTEM_PROMPT = """Ты — опытный помощник, извлекающий параметры для маршрута по городу из свободного текста пользователя.

═══════════════════════════════════════════════════
ФОРМАТ ОТВЕТА
═══════════════════════════════════════════════════
Верни ТОЛЬКО валидный JSON, БЕЗ markdown, БЕЗ ```json, БЕЗ пояснений:
{
  "city": <название города в именительном падеже или null>,
  "time": <число часов или null>,
  "budget": <бюджет в рублях или null>,
  "category_ids": [<список id>],
  "keywords": [<ключевые слова>]
}

═══════════════════════════════════════════════════
ДОСТУПНЫЕ КАТЕГОРИИ
═══════════════════════════════════════════════════
1 — Достопримечательности (памятники, исторические места, архитектура)
2 — Музеи (выставки, галереи, музеи)
3 — Парки и прогулки (парки, скверы, набережные, сады)
4 — Гастрономия (рестораны, кафе, еда, кухня)
5 — Развлечения и активный отдых (аквапарки, кино, картинг, аттракционы)
6 — Религиозные объекты (храмы, мечети, соборы, синагоги)

═══════════════════════════════════════════════════
ПРАВИЛА ИЗВЛЕЧЕНИЯ
═══════════════════════════════════════════════════

🏙 ГОРОД (city):
- «в Москве» → "Москва"
- «в Казани» → "Казань"
- «в Краснодаре» → "Краснодар"
- «в Екатеринбурге» → "Екатеринбург"
- «в Питере», «в Санкт-Петербурге» → "Санкт-Петербург"
- Город возвращай в ИМЕНИТЕЛЬНОМ падеже.
- Если город не упомянут — null.

⏱ ВРЕМЯ — ОЧЕНЬ ВАЖНО:
Извлекай число часов из ЛЮБОГО упоминания времени.

ПРЯМЫЕ ЧИСЛА (самое главное):
- «1 час», «один час» → 1
- «2 часа», «два часа», «на 2 часа» → 2
- «3 часа», «три часа», «на 3 часа» → 3
- «4 часа», «четыре часа», «на 4 часа» → 4
- «5 часов», «пять часов», «на 5 часов» → 5
- «6 часов», «шесть часов», «на 6 часов» → 6
- «на N часов» — ВСЕГДА возвращай N

СЛОВЕСНЫЕ СИНОНИМЫ:
- «недолго», «не долго», «ненадолго» → 2
- «быстро», «быстренько», «коротко» → 2
- «пару часов», «часа два» → 2
- «полдня» → 4
- «подольше», «надолго», «долго» → 5
- «весь день», «целый день» → 6

⚠️ КРИТИЧЕСКОЕ ПРАВИЛО:
ЕСЛИ в тексте есть ЛЮБОЕ число перед словом «час/часа/часов» —
верни ЭТО ЧИСЛО. НЕ возвращай null!

💰 БЮДЖЕТ (budget):
- «средний бюджет», «средне» → 1500
- «бюджетно», «подешевле», «экономно» → 500
- «дорого», «премиум» → 5000
- «до 500» → 500
- «до 1000», «тыща» → 1000
- «до 1500» → 1500
- «до 3000» → 3000
- «бесплатно» → 0
- «без ограничений» → null
- Если не указано — null

📂 КАТЕГОРИИ (category_ids):
- «музеи», «выставки», «галереи» → [2]
- «парки», «погулять», «скверы», «набережная» → [3]
- «поесть», «покушать», «рестораны», «кафе», «еда» → [4]
- «развлечения», «активный отдых», «покататься» → [5]
- «храмы», «мечети», «церкви» → [6]
- «достопримечательности», «памятники», «история» → [1]

🎯 КОМБИНАЦИИ:
- «с детьми» → добавь [3, 5]
- «романтика», «свидание» → добавь [3, 4]
- «культурная программа» → [1, 2, 6]
- «поесть и погулять» → [3, 4]
- «музеи и парки» → [2, 3]

🔍 КЛЮЧЕВЫЕ СЛОВА (keywords):
Собери важные слова: «дети», «романтика», «спокойно», «активно», «семья», «свидание», «друзья», «недорого».

═══════════════════════════════════════════════════
ПРИМЕРЫ
═══════════════════════════════════════════════════

Запрос: «маршрут по москве на 2 часа музеи парки и кафе»
Ответ: {"city": "Москва", "time": 2, "budget": null, "category_ids": [2, 3, 4], "keywords": []}

Запрос: «хочу на 3 часа в казань»
Ответ: {"city": "Казань", "time": 3, "budget": null, "category_ids": [], "keywords": []}

Запрос: «хочу погулять 3 часа с детьми, бюджет 1000»
Ответ: {"city": null, "time": 3, "budget": 1000, "category_ids": [3, 5], "keywords": ["дети"]}

Запрос: «Романтический вечер, бюджет 3000»
Ответ: {"city": null, "time": null, "budget": 3000, "category_ids": [3, 4], "keywords": ["романтика", "вечер"]}

Запрос: «На пару часиков, хочу музеи и поесть»
Ответ: {"city": null, "time": 2, "budget": null, "category_ids": [2, 4], "keywords": []}

Запрос: «Культурная программа на полдня в краснодаре»
Ответ: {"city": "Краснодар", "time": 4, "budget": null, "category_ids": [1, 2, 6], "keywords": ["культурная"]}

Запрос: «Быстро посмотреть достопримечательности, тыща рублей»
Ответ: {"city": null, "time": 2, "budget": 1000, "category_ids": [1], "keywords": []}

Запрос: «Погулять по паркам и поесть, недорого»
Ответ: {"city": null, "time": null, "budget": 500, "category_ids": [3, 4], "keywords": ["недорого"]}

Запрос: «Хочу просто погулять»
Ответ: {"city": null, "time": null, "budget": null, "category_ids": [], "keywords": []}

Запрос: «средний бюджет в москве на недолго»
Ответ: {"city": "Москва", "time": 2, "budget": 1500, "category_ids": [], "keywords": []}

Запрос: «погулять в питере 5 часов»
Ответ: {"city": "Санкт-Петербург", "time": 5, "budget": null, "category_ids": [3], "keywords": []}

═══════════════════════════════════════════════════
ВАЖНО
═══════════════════════════════════════════════════
- ВСЕГДА возвращай ТОЛЬКО JSON.
- Если что-то не указано — null или [].
- Числа часов всегда определяй из текста, если есть.
"""


# ============================================================
# ФОЛБЭК-ФУНКЦИИ (если GigaChat не понял)
# ============================================================

def _fallback_time_from_text(text: str) -> int | None:
    """
    Фолбэк: если GigaChat не понял время — ищем в тексте.
    """
    if not text:
        return None

    text_lower = text.lower()

    # 1. Ищем явное число перед «час/часа/часов»
    # Примеры: «на 2 часа», «2 часа», «через 3 часа», «за 4 часа»
    match = re.search(r"(\d+)\s*час", text_lower)
    if match:
        try:
            hours = int(match.group(1))
            if 1 <= hours <= 24:
                return hours
        except (TypeError, ValueError):
            pass

    # 2. Словесные синонимы для 2 часов
    short_keywords = [
        "недолго", "не долго", "ненадолго",
        "быстро", "быстренько", "коротко",
        "пару часов", "часа два",
    ]
    for kw in short_keywords:
        if kw in text_lower:
            return 2

    # 3. Полдня
    if "полдня" in text_lower or "пол дня" in text_lower:
        return 4

    # 4. Долгие прогулки
    if any(kw in text_lower for kw in ["подольше", "надолго", "долго"]):
        return 5

    # 5. Весь день
    if any(kw in text_lower for kw in ["весь день", "целый день"]):
        return 6

    return None


def _fallback_budget_from_text(text: str) -> int | None:
    """
    Фолбэк: если GigaChat не понял бюджет — ищем в тексте.
    """
    if not text:
        return None

    text_lower = text.lower()

    # Явные числа перед «руб/₽»
    match = re.search(r"(\d{2,6})\s*(?:руб|₽|р\b)", text_lower)
    if match:
        try:
            budget = int(match.group(1))
            if 0 <= budget <= 1000000:
                return budget
        except (TypeError, ValueError):
            pass

    # Средний бюджет
    if "средний бюджет" in text_lower or "средне" in text_lower:
        return 1500

    # Бюджетно
    if any(kw in text_lower for kw in ["бюджетно", "подешевле", "экономно"]):
        return 500

    # Дорого
    if any(kw in text_lower for kw in ["дорого", "премиум"]):
        return 5000

    # Бесплатно
    if "бесплатно" in text_lower:
        return 0

    # Без ограничений
    if "без ограничений" in text_lower:
        return None

    # «до N»
    match = re.search(r"до\s*(\d{2,6})", text_lower)
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            pass

    return None


def _fallback_city_from_text(text: str) -> str | None:
    """
    Фолбэк: ищет город в тексте.
    Возвращает название в правильном регистре.
    """
    if not text:
        return None

    # Известные города и их синонимы
    # Первое значение — правильное название
    known_cities = {
        "москва": "Москва",
        "москве": "Москва",
        "москву": "Москва",
        "казань": "Казань",
        "казани": "Казань",
        "краснодар": "Краснодар",
        "краснодаре": "Краснодар",
        "екатеринбург": "Екатеринбург",
        "екатеринбурге": "Екатеринбург",
        "питер": "Санкт-Петербург",
        "питере": "Санкт-Петербург",
        "санкт-петербург": "Санкт-Петербург",
        "санкт-петербурге": "Санкт-Петербург",
        "новосибирск": "Новосибирск",
        "новосибирске": "Новосибирск",
    }

    text_lower = text.lower()

    for key, city_name in known_cities.items():
        if re.search(rf"\b{re.escape(key)}\b", text_lower):
            return city_name

    return None


def _fallback_categories_from_text(text: str) -> list[int]:
    """
    Фолбэк: ищет категории по ключевым словам.
    """
    if not text:
        return []

    text_lower = text.lower()
    categories = set()

    # 1 — Достопримечательности
    if any(kw in text_lower for kw in [
        "достопримечательност", "памятник", "историческ",
        "архитектур", "кремл", "экскурси",
    ]):
        categories.add(1)

    # 2 — Музеи
    if any(kw in text_lower for kw in [
        "музе", "выставк", "галере", "экспозиц",
    ]):
        categories.add(2)

    # 3 — Парки
    if any(kw in text_lower for kw in [
        "парк", "погуля", "сквер", "набережн",
        "сад", "зелён", "зелен",
    ]):
        categories.add(3)

    # 4 — Гастрономия
    if any(kw in text_lower for kw in [
        "ресторан", "кафе", "поесть", "покуша", "еда",
        "кухн", "кофе", "обед", "ужин", "завтрак",
    ]):
        categories.add(4)

    # 5 — Развлечения
    if any(kw in text_lower for kw in [
        "развлечен", "активн", "поката", "аттракцион",
        "аквапарк", "кино", "картинг",
    ]):
        categories.add(5)

    # 6 — Религиозные
    if any(kw in text_lower for kw in [
        "храм", "мечет", "церк", "собор", "синагог",
        "религи",
    ]):
        categories.add(6)

    return sorted(categories)


# ============================================================
# ПАРСИНГ ОТВЕТА GigaChat
# ============================================================

def _parse_json_response(raw: str, default: dict) -> dict:
    """Извлекает JSON из ответа GigaChat."""
    raw = raw.strip()

    # Убираем markdown-обёртки
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # Ищем JSON (объект или массив)
    match = re.search(r"[\{\[].*[\}\]]", raw, re.DOTALL)
    if not match:
        logging.warning(f"JSON не найден: {raw}")
        return default

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        logging.warning(f"JSON невалидный: {match.group()}")
        return default

    # Если GigaChat вернул список — берём первый объект
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            data = data[0]
        else:
            logging.warning(f"JSON — список, но не объектов: {data!r}")
            return default

    if not isinstance(data, dict):
        logging.warning(f"JSON — не словарь: {type(data).__name__}")
        return default

    result = default.copy()

    # Город
    city_raw = data.get("city")
    if city_raw and isinstance(city_raw, str):
        result["city"] = city_raw.strip()
    else:
        result["city"] = None

    # Остальные поля
    result["time"] = _safe_int(data.get("time"))
    result["budget"] = _safe_int(data.get("budget"))
    result["category_ids"] = _safe_list_int(data.get("category_ids"))
    result["keywords"] = _safe_list_str(data.get("keywords"))

    # Категории — только 1–6, без дубликатов
    result["category_ids"] = sorted(set(
        c for c in result["category_ids"] if 1 <= c <= 6
    ))
    result["keywords"] = list(set(result["keywords"]))

    return result


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_list_int(value) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for v in value:
        try:
            result.append(int(v))
        except (TypeError, ValueError):
            continue
    return result


def _safe_list_str(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v]


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================

def parse_user_request(text: str) -> dict:
    """
    Отправляет текст в GigaChat, получает структурированные параметры.
    Если GigaChat не понял — использует фолбэк.
    """
    default = {
        "city": None,
        "time": None,
        "budget": None,
        "category_ids": [],
        "keywords": [],
    }

    if not text or not text.strip():
        return default

    result = default.copy()

    # ========================================================
    # 1. ПРОБУЕМ GigaChat
    # ========================================================
    if GIGACHAT_CREDENTIALS:
        try:
            with GigaChat(
                base_url="https://api.giga.chat/v2",
                credentials=GIGACHAT_CREDENTIALS,
                model="GigaChat-2",
                ca_bundle_file="Russian_Trusted_Root_CA.cer",
            ) as client:

                full_prompt = (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"Запрос: «{text}»\n"
                    f"Ответ:"
                )
                response = client.chat.create(full_prompt)

                raw = response.messages[0].content[0].text
                logging.info(f"GigaChat ответ: {raw}")

                result = _parse_json_response(raw, default)

        except Exception:
            logging.exception("Ошибка GigaChat — использую фолбэк")
    else:
        logging.warning("GIGACHAT_CREDENTIALS не задан — использую фолбэк")

    # ========================================================
    # 2. ФОЛБЭК — если GigaChat не понял
    # ========================================================

    # Город
    if not result.get("city"):
        fallback_city = _fallback_city_from_text(text)
        if fallback_city:
            result["city"] = fallback_city
            logging.info(f"Фолбэк city: {fallback_city}")

    # Время
    if result.get("time") is None:
        fallback_time = _fallback_time_from_text(text)
        if fallback_time is not None:
            result["time"] = fallback_time
            logging.info(f"Фолбэк time: {fallback_time}")

    # Бюджет
    if result.get("budget") is None:
        fallback_budget = _fallback_budget_from_text(text)
        if fallback_budget is not None:
            result["budget"] = fallback_budget
            logging.info(f"Фолбэк budget: {fallback_budget}")

    # Категории — если пусто, ищем по ключевым словам
    if not result.get("category_ids"):
        fallback_cats = _fallback_categories_from_text(text)
        if fallback_cats:
            result["category_ids"] = fallback_cats
            logging.info(f"Фолбэк category_ids: {fallback_cats}")

    logging.info(f"Финальные параметры: {result}")
    return result