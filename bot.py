# ИМПОРТ ЯДРА БОТА

from app.bot_core import bot, dp

print(
    f"DEBUG [bot.py]: imported, dp id = {id(dp)}",
    flush=True,
)


# ОСТАЛЬНЫЕ ИМПОРТЫ

import asyncio
import logging
import os
import json
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models import User, Place, Category, City, SavedRoute

from services.share_service import generate_route_pdf
from services.map_service import (
    generate_route_map,
    generate_yandex_maps_link,
)
from services.ai_service import parse_user_request
from services.admin_service import import_from_json
from services.export_service import (
    export_db_to_json,
    export_city_to_json,
)
from services.weather_service import (
    get_weather,
    filter_places_by_weather,
)
from services.schedule_service import filter_places_by_schedule

from dotenv import load_dotenv

from maxapi.types import InputMedia
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.types import (
    MessageCreated,
    MessageCallback,
    CallbackButton,
    Command,
    MessageButton,
)

try:
    from maxapi.context.isolation import SimpleEventIsolation
    _HAS_ISOLATION = True
except ImportError:
    _HAS_ISOLATION = False


from services.route_service import (
    build_route,
    calculate_route_distance,
)


# НАСТРОЙКА


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

load_dotenv()

_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {
    int(x.strip())
    for x in _admin_ids_raw.split(",")
    if x.strip().isdigit()
}
logging.info(f"Админов загружено: {len(ADMIN_IDS)}")


# ВРЕМЯ (MSK)

MSK = ZoneInfo("Europe/Moscow")


def _fmt_msk(dt, fmt: str = "%d.%m %H:%M") -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime(fmt)


def _msk_now_str(fmt: str = "%d.%m %H:%M") -> str:
    return datetime.now(MSK).strftime(fmt)


# ОТПРАВКА СООБЩЕНИЙ

def _extract_user_id(event) -> int | None:
    """Пытается достать user_id из любого типа события."""
    # BotStarted
    if hasattr(event, "user") and event.user is not None:
        uid = (
            getattr(event.user, "user_id", None)
            or getattr(event.user, "id", None)
        )
        if uid:
            return uid

    # MessageCreated / MessageCallback
    if hasattr(event, "message") and event.message is not None:
        sender = getattr(event.message, "sender", None)
        if sender is not None:
            uid = getattr(sender, "user_id", None)
            if uid:
                return uid

    # Callback
    if hasattr(event, "callback") and event.callback is not None:
        user = getattr(event.callback, "user", None)
        if user is not None:
            uid = (
                getattr(user, "user_id", None)
                or getattr(user, "id", None)
            )
            if uid:
                return uid

    # chat_id как фолбэк
    if hasattr(event, "chat_id"):
        return event.chat_id

    return None


async def send(event, text: str, keyboard=None) -> bool:
    kwargs = {}
    if keyboard is not None:
        kwargs["attachments"] = [keyboard]

    # 1. Обычное событие с message
    if hasattr(event, "message") and event.message is not None:
        try:
            await event.message.answer(text, **kwargs)
            return True
        except Exception:
            logging.exception("message.answer не сработал")

    # 2. BotStarted — event.send
    if hasattr(event, "send") and callable(getattr(event, "send", None)):
        try:
            await event.send(text, **kwargs)
            return True
        except Exception:
            logging.exception("event.send не сработал")

    # 3. Фолбэк — bot.send_message
    user_id = _extract_user_id(event)
    if user_id:
        try:
            await bot.send_message(user_id, text, **kwargs)
            return True
        except Exception:
            logging.exception("bot.send_message не сработал")

    logging.error("Не удалось отправить сообщение: нет доступного метода")
    return False


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def send_pdf(event, pdf_path) -> bool:
    path_str = str(pdf_path)

    try:
        media = InputMedia(path=path_str)
        await event.message.answer(
            "Маршрут (PDF)",
            attachments=[media],
        )
        return True
    except Exception:
        logging.exception("Ошибка при отправке PDF")

    await send(event, f"PDF сохранён по пути:\n{path_str}")
    return False


# ЗАЩИТА ОТ ДУБЛЕЙ ПО mid

_seen_mids = set()
_SEEN_MIDS_LIMIT = 5000


def _get_mid(event):
    if hasattr(event, "message") and event.message:
        body = getattr(event.message, "body", None)
        if body:
            mid = getattr(body, "mid", None)
            if mid:
                return mid

    if hasattr(event, "callback") and event.callback:
        mid = getattr(event.callback, "mid", None)
        if mid:
            return mid

    return None


def _is_duplicate_mid(event) -> bool:
    mid = _get_mid(event)
    if mid is None:
        return False

    if mid in _seen_mids:
        return True

    _seen_mids.add(mid)

    if len(_seen_mids) > _SEEN_MIDS_LIMIT:
        _seen_mids.clear()

    return False


# СОСТОЯНИЯ

user_route_settings = {}
user_last_route = {}


def _default_settings() -> dict:
    return {"time": None, "budget": None, "categories": []}


def _get_or_create_settings(user_id: int) -> dict:
    if user_id not in user_route_settings:
        user_route_settings[user_id] = _default_settings()
    return user_route_settings[user_id]


# БЕЗОПАСНЫЙ ДОСТУП

def _safe_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_price(value) -> int:
    if value is None or value == "":
        return 0

    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    try:
        cleaned = "".join(
            ch for ch in str(value)
            if ch.isdigit() or ch in ".-"
        )
        if not cleaned:
            return 0
        return int(float(cleaned))
    except (TypeError, ValueError):
        return 0


def _serialize_place(place) -> dict:
    return {
        "name": _safe_str(getattr(place, "name", None)) or "Без названия",
        "address": _safe_str(getattr(place, "address", None)) or "Адрес не указан",
        "price": _safe_price(getattr(place, "price", None)),
        "description": _safe_str(getattr(place, "description", None)),
        "ticket_url": _safe_str(getattr(place, "ticket_url", None)),
        "source_url": _safe_str(getattr(place, "source_url", None)),
        "latitude": getattr(place, "latitude", None),
        "longitude": getattr(place, "longitude", None),
        "opening_hours": _safe_str(getattr(place, "opening_hours", None)),
    }


# КОНТЕКСТНЫЕ ФИЛЬТРЫ (погода + время работы)

def _apply_context_filters(session, places: list, city) -> tuple[list, list, dict]:
    reasons = []
    weather = {
        "is_bad": False,
        "icon": "🌍",
        "description": "",
        "temp": None,
    }

    try:
        if city and city.latitude and city.longitude:
            weather = get_weather(city.latitude, city.longitude)
            places, weather_reason = filter_places_by_weather(places, weather)
            if weather_reason:
                reasons.append(weather_reason)
    except Exception:
        logging.exception("Ошибка погодного фильтра")

    try:
        places, schedule_reason = filter_places_by_schedule(places)
        if schedule_reason:
            reasons.append(schedule_reason)
    except Exception:
        logging.exception("Ошибка фильтра по расписанию")

    return places, reasons, weather


# ГОРОДА

OTHER_CITY_ID = -1


def _get_all_cities(session) -> list:
    try:
        return session.query(City).order_by(City.name).all()
    except Exception:
        logging.exception("Не удалось загрузить города из БД")
        return []


def _get_city_by_id(session, city_id) -> "City | None":
    try:
        return session.query(City).filter(City.id == city_id).first()
    except Exception:
        logging.exception(f"Не удалось найти город id={city_id}")
        return None


def _city_name(session, city_id) -> str:
    if city_id is None:
        return "Не выбран"
    city = _get_city_by_id(session, city_id)
    return city.name if city else "Неизвестный город"


# КЛАВИАТУРЫ — АДМИНКА

def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="📊 База данных", payload="admin:db"))
    builder.row(CallbackButton(text="📥 Импорт JSON", payload="admin:import"))
    builder.row(CallbackButton(text="📤 Экспорт всей БД", payload="admin:export"))
    builder.row(
        CallbackButton(
            text="📤 Экспорт одного города",
            payload="admin:export_city_menu",
        )
    )
    builder.row(CallbackButton(text="🏠 Главное меню", payload="main_menu"))
    return builder.as_markup()


def admin_db_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="📊 Статистика", payload="admin:stats"))
    builder.row(CallbackButton(text="🏙 Список городов", payload="admin:cities"))
    builder.row(
        CallbackButton(text="📂 Список категорий", payload="admin:categories")
    )
    builder.row(
        CallbackButton(text="Мест без координат", payload="admin:no_coords")
    )
    builder.row(CallbackButton(text="⬅️ Назад", payload="admin:back"))
    return builder.as_markup()


def admin_import_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📥 Скачать шаблон JSON", payload="admin:template")
    )
    builder.row(
        CallbackButton(text="📄 Скачать инструкцию", payload="admin:instructions")
    )
    builder.row(CallbackButton(text="⬅️ Назад", payload="admin:back"))
    return builder.as_markup()


def admin_export_city_keyboard(session):
    builder = InlineKeyboardBuilder()
    cities = _get_all_cities(session)

    if not cities:
        builder.row(CallbackButton(text="Городов нет", payload="noop"))
    else:
        for city in cities:
            builder.row(
                CallbackButton(
                    text=city.name,
                    payload=f"admin:export_city:{city.id}",
                )
            )

    builder.row(CallbackButton(text="⬅️ Назад", payload="admin:back"))
    return builder.as_markup()


# КЛАВИАТУРЫ — ПОЛЬЗОВАТЕЛЬ

def city_keyboard(session, back_payload: str | None = None):
    builder = InlineKeyboardBuilder()
    cities = _get_all_cities(session)

    if not cities:
        builder.row(
            CallbackButton(text="Города пока не добавлены", payload="noop")
        )
    else:
        for city in cities:
            builder.row(
                CallbackButton(text=city.name, payload=f"city:{city.id}")
            )

    builder.row(CallbackButton(text="🌍 Другой город", payload="city:other"))

    if back_payload:
        builder.row(CallbackButton(text="⬅️ Назад", payload=back_payload))

    return builder.as_markup()


def main_menu_keyboard(user_id: int | None = None, city_id: int | None = None):
    builder = InlineKeyboardBuilder()

    builder.row(
        CallbackButton(text="Построить маршрут", payload="build_route")
    )
    builder.row(
        CallbackButton(text="Мои маршруты", payload="my_routes")
    )
    builder.row(
        CallbackButton(text="Сменить город", payload="change_city")
    )

    if user_id and city_id:
        from maxapi.types import LinkButton

        app_url = (
            f"https://max.ru/se14349358_bot"
            f"?startapp={user_id}:{city_id}"
        )

        builder.row(
            LinkButton(
                text="Выбрать места вручную",
                url=app_url,
            )
        )

    return builder.as_markup()


def start_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        MessageButton(text="/start")
    )
    return builder.as_markup()


def after_route_keyboard(map_link: str | None = None):
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="📄 Скачать PDF", payload="route:pdf")
    )
    builder.row(
        CallbackButton(text="💾 Сохранить маршрут", payload="route:save")
    )
    if map_link:
        try:
            from maxapi.types import LinkButton
            builder.row(LinkButton(text="Открыть карту", url=map_link))
        except ImportError:
            logging.warning("LinkButton недоступен")
    builder.row(
        CallbackButton(text="🏠 Главное меню", payload="main_menu"),
        CallbackButton(text="🔄 Построить ещё раз", payload="build_route"),
    )
    return builder.as_markup()


def after_pdf_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(CallbackButton(text="🏠 Главное меню", payload="main_menu"))
    builder.row(
        CallbackButton(text="🔄 Построить ещё раз", payload="build_route")
    )
    return builder.as_markup()


def time_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="2 часа", payload="time:2"),
        CallbackButton(text="3 часа", payload="time:3"),
    )
    builder.row(
        CallbackButton(text="4 часа", payload="time:4"),
        CallbackButton(text="5 часов", payload="time:5"),
    )
    builder.row(CallbackButton(text="6 часов", payload="time:6"))
    builder.row(CallbackButton(text="⬅️ Назад", payload="main_menu"))
    return builder.as_markup()


def budget_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(text="Бесплатно", payload="budget:0"),
        CallbackButton(text="До 500 ₽", payload="budget:500"),
    )
    builder.row(
        CallbackButton(text="До 1000 ₽", payload="budget:1000"),
        CallbackButton(text="До 1500 ₽", payload="budget:1500"),
    )
    builder.row(
        CallbackButton(text="До 3000 ₽", payload="budget:3000"),
        CallbackButton(text="Без ограничений", payload="budget:none"),
    )
    builder.row(CallbackButton(text="⬅️ Назад", payload="build_route"))
    return builder.as_markup()


def category_keyboard(categories, selected):
    builder = InlineKeyboardBuilder()

    for category in categories:
        text = (
            f"✅ {category.name}" if category.id in selected
            else category.name
        )
        builder.row(
            CallbackButton(text=text, payload=f"category:{category.id}")
        )

    builder.row(
        CallbackButton(text="🚀 Построить маршрут", payload="route:generate")
    )
    builder.row(CallbackButton(text="⬅️ Назад", payload="back_to_budget"))
    return builder.as_markup()


# ВСПОМОГАТЕЛЬНЫЕ

def _get_user(session, user_id: int):
    return session.query(User).filter(
        User.max_user_id == user_id
    ).first()


async def _send_main_menu(
    event,
    city_name: str,
    user_id: int | None = None,
    city_id: int | None = None,
):
    await send(
        event,
        f"🏙️ Выбранный город: {city_name}\n\n"
        "Что хочешь сделать?\n\n"
        "❗Если хочешь выбрать места вручную - открой приложение."
        " Там ты сможешь свайпать карточки и лайкать "
        "любимые места. А так же бот может построить маршрут "
        "по твоему выбору",
        keyboard=main_menu_keyboard(
            user_id=user_id,
            city_id=city_id,
        ),
    )


def _send_city_choice(session, event, back_payload: str | None = None):
    return send(
        event,
        "Выбери город:",
        keyboard=city_keyboard(session, back_payload=back_payload),
    )


def _format_route_message(
    route_data: list,
    total_distance: float,
    total_price: int,
    hours: int,
    budget,
    category_names: list,
    weather: dict,
    reasons: list,
    city_name: str = "—",
    text: str | None = None,
) -> str:
    message = ""

    if text:
        message += "🤖 Маршрут по вашему запросу\n\n"
        message += f"💬 {text}\n\n"

    if city_name and city_name != "—":
        message += f"🏙️ Город: {city_name}\n"

    message += f"🕓 Время: около {hours} ч.\n"

    message += (
        "💰 Бюджет: без ограничений\n"
        if budget is None
        else f"💰 Бюджет: до {budget} ₽\n"
    )

    if category_names:
        message += f"❤️ Интересы: {', '.join(category_names)}\n"

    if weather.get("description"):
        message += (
            f"{weather.get('icon', '🌍')} Погода: "
            f"{weather['description']}, "
            f"{weather.get('temp', '?')}°C\n"
        )

    if reasons:
        message += "\n"
        for reason in reasons:
            message += f"{reason}\n"

    message += "\n📍 Маршрут:\n\n"

    for index, place in enumerate(route_data, start=1):
        price_text = (
            "бесплатно" if place["price"] == 0
            else f"{place['price']} ₽"
        )

        message += (
            f"{index}. {place['name']}\n"
            f"📌 {place['address']}\n"
            f"💰 {price_text}\n"
        )

        if place.get("opening_hours"):
            message += f"⏰ {place['opening_hours']}\n"

        if place.get("description"):
            message += f"ℹ️ {place['description']}\n"

        if place.get("ticket_url"):
            message += f"🎟 Билеты: {place['ticket_url']}\n"

        if place.get("source_url"):
            message += f"🌐 Узнать больше: {place['source_url']}\n"

        message += "\n"

    message += (
        "━━━━━━━━━━━━━━\n"
        f"🚶 Расстояние: {total_distance:.1f} км\n"
        f"💰 Стоимость: {total_price} ₽\n"
        f"📍 Мест: {len(route_data)}"
    )

    return message


# AI-МАРШРУТ

async def _build_ai_route(event, session, user_id: int, text: str, params: dict):
    user = _get_user(session, user_id)
    if not user:
        await send(event, "Сначала нажмите /start.")
        return

    city = None

    city_from_text = params.get("city")
    if city_from_text:
        city = session.query(City).filter(
            City.name.ilike(f"%{city_from_text}%")
        ).first()

        if city:
            logging.info(f"Город из текста: {city.name} (id={city.id})")
        else:
            logging.warning(f"Город «{city_from_text}» не найден в БД")

    if city and city_from_text:
        if user.city_id != city.id:
            user.city_id = city.id
            session.commit()
            logging.info(f"Обновили город пользователя: {city.name}")

    if not city and city_from_text:
        cities = _get_all_cities(session)
        city_names = ", ".join(c.name for c in cities)
        await send(
            event,
            f"😔 Город «{city_from_text}» не найден.\n\n"
            f"Доступные города: {city_names}",
            keyboard=city_keyboard(session),
        )
        return

    if not city:
        if not user.city_id:
            await send(
                event,
                "Сначала выберите город. Отправьте /start.",
                keyboard=city_keyboard(session),
            )
            return
        city = session.query(City).filter(City.id == user.city_id).first()
        if not city:
            await send(event, "❌ Город не найден.")
            return
        logging.info(f"Город из профиля: {city.name} (id={city.id})")

    city_id = city.id

    places = session.query(Place).filter(
        Place.city_id == city_id,
        Place.is_active == True,
    ).all()

    places, reasons, weather = _apply_context_filters(session, places, city)

    route = build_route(
        places,
        min_places=3,
        max_places=8,
        max_budget=params.get("budget"),
        max_time_hours=params.get("time"),
        category_ids=params.get("category_ids") or None,
    )

    if not route:
        await send(
            event,
            "😔 Не удалось найти подходящий маршрут.\n\n"
            "Попробуйте описать иначе или изменить параметры.",
        )
        return

    route_data = [_serialize_place(p) for p in route]
    total_distance = calculate_route_distance(route)
    total_price = sum(item["price"] for item in route_data)

    message = _format_route_message(
        route_data=route_data,
        total_distance=total_distance,
        total_price=total_price,
        hours=params["time"],
        budget=params.get("budget"),
        category_names=[],
        weather=weather,
        reasons=reasons,
        city_name=_city_name(session, city_id),
        text=text,
    )

    map_path = None
    try:
        import uuid as _uuid
        map_id = _uuid.uuid4().hex[:8]
        map_path = generate_route_map(route_data, route_id=map_id)
        if map_path:
            media = InputMedia(path=str(map_path))
            await event.message.answer(
                "Карта маршрута:",
                attachments=[media],
            )
    except Exception:
        logging.exception("Не удалось отправить карту")

    map_link = None
    try:
        map_link = generate_yandex_maps_link(route_data)
    except Exception:
        logging.exception("Не удалось создать ссылку")

    user_last_route[user_id] = {
        "city_name": _city_name(session, city_id),
        "hours": params["time"],
        "budget_text": (
            f"до {params['budget']} ₽" if params.get("budget")
            else "без ограничений"
        ),
        "category_names": [],
        "places": route_data,
        "total_distance": total_distance,
        "total_price": total_price,
        "map_path": str(map_path) if map_path else None,
        "map_link": map_link,
    }

    await send(
        event,
        message,
        keyboard=after_route_keyboard(map_link=map_link),
    )


async def _handle_ai_text(event, user_id: int):
    body = getattr(event.message, "body", None)
    if not body:
        return

    text = getattr(body, "text", None)
    if not text:
        return

    text = text.strip()

    if text.startswith("/") or len(text) < 5:
        return

    logging.info(f">>> AI-запрос от {user_id}: {text}")

    session = SessionLocal()

    try:
        user = _get_user(session, user_id)
        if not user or not user.city_id:
            await send(
                event,
                "Для начала работы бота отправьте /start.",
                keyboard=start_keyboard(),
            )
            return

        await send(event, "🤔 Анализирую запрос...")

        try:
            params = parse_user_request(text)
        except Exception:
            logging.exception("Ошибка AI-парсинга")
            await send(event, "❌ Не удалось разобрать запрос.")
            return

        logging.info(f"AI-параметры: {params}")

        if not params.get("time"):
            user_route_settings[user_id] = {
                "ai_mode": True,
                "ai_text": text,
                "ai_params": params,
            }
            await send(
                event,
                "🤔 Понял ваш запрос, но не хватает времени.\n\n"
                "⏱ На сколько часов планируете прогулку?",
                keyboard=time_keyboard(),
            )
            return

        if not params.get("budget"):
            params["budget"] = None
        if not params.get("category_ids"):
            params["category_ids"] = [1, 2, 3, 4, 5, 6]

        await _build_ai_route(event, session, user_id, text, params)

    except Exception:
        logging.exception("Ошибка AI-обработчика")
        await send(event, "❌ Произошла ошибка. Попробуйте ещё раз.")
    finally:
        session.close()


# ПЕРВЫЙ ЗАПУСК БОТА (bot_started)

@dp.bot_started()
async def bot_started_handler(event):
    user_id = _extract_user_id(event)
    logging.info(f">>> BOT_STARTED user={user_id}")

    if not user_id:
        logging.error("Не удалось определить user_id в bot_started")
        return

    session = SessionLocal()

    try:
        user = _get_user(session, user_id)

        # Уже знакомы и город выбран → сразу главное меню
        if user and user.city_id:
            await _send_main_menu(
                event,
                _city_name(session, user.city_id),
                user_id=user_id,
                city_id=user.city_id,
            )
            return

        # Первый раз → приветствие + кнопка /start
        await send(
            event,
            "👋 Привет!\n\n"
            "👇 Для начала работы напиши /start",
            keyboard=start_keyboard(),
        )

    except Exception:
        logging.exception("Ошибка в bot_started_handler")
    finally:
        try:
            session.rollback()
        except Exception:
            logging.exception("rollback failed")
        try:
            session.close()
        except Exception:
            logging.exception("close failed")


# /START

@dp.message_created(Command("start"))
async def start_handler(event: MessageCreated):

    if _is_duplicate_mid(event):
        logging.info("Пропущен дубликат /start (mid)")
        return

    user_id = event.message.sender.user_id
    logging.info(f">>> START user={user_id}")

    session = SessionLocal()

    try:
        user = _get_user(session, user_id)

        if not user:
            await send(
                event,
                "👋 Привет! Я Hermes AI — твой личный навигатор по городу\n\n"
                    "Надоело думать, куда пойти и чем заняться? "
                    "Я всё придумаю за тебя 😎\n\n"
                    "🔥 Что умею:\n"
                    "• Соберу маршрут из интересных мест.\n"
                    "• ️Подберу места под твои интересы.\n"
                    "• Учту, сколько у тебя есть времени.\n"
                    "• Подстроюсь под твой бюджет.\n"
                    "• Посмотрю погоду и исключу то, что сейчас не подходит.\n"
                    "• Учту время работы заведений и мест.\n"
                    "• Покажу маршрут на карте.\n"
                    "• Сделаю красивый PDF, чтобы сохранить или отправить другу.\n"
                    "• Сохраню маршрут — вернёшься к нему когда захочешь.\n"
                    "• А ещё можно самому выбирать места в приложении.\n\n"
                    "Если хочешь - можно вообще без заморочек. "
                    " Просто напиши, чего хочется, например:\n\n"
                    "«Есть 4 часа, хочу красивые места, вкусно поесть "
                    "и потратить максимум 1500 ₽»\n\n"
                    "И я соберу тебе план\n\n"
                    "👇 Для начала выбери город — и погнали!",
                    keyboard=city_keyboard(session),
            )
            return

        if not user.city_id:
            await send(
                event,
                "👋 С возвращением!\n\n"
                "Осталось выбрать город:",
                keyboard=city_keyboard(session),
            )
            return

        await _send_main_menu(
            event,
            _city_name(session, user.city_id),
            user_id=user_id,
            city_id=user.city_id,
        )

        logging.info(f"<<< START user={user_id}")

    except Exception:
        logging.exception("Ошибка при обработке /start")

    finally:
        try:
            session.rollback()
        except Exception:
            logging.exception("rollback failed")
        try:
            session.close()
        except Exception:
            logging.exception("close failed")


# /admin

@dp.message_created(Command("admin"))
async def admin_handler(event: MessageCreated):
    user_id = event.message.sender.user_id

    if not _is_admin(user_id):
        await send(event, "Команда недоступна.")
        return

    logging.info(f">>> ADMIN user={user_id}")
    await send(
        event,
        "Админ-панель\n\nВыбери раздел:",
        keyboard=admin_keyboard(),
    )


# ЕДИНЫЙ ОБРАБОТЧИК (файл + AI)

@dp.message_created()
async def unified_handler(event: MessageCreated):
    user_id = event.message.sender.user_id

    body = getattr(event.message, "body", None)
    if not body:
        return

    attachments = getattr(body, "attachments", None) or []

    if attachments and _is_admin(user_id):
        file_payload = None
        for att in attachments:
            att_type = getattr(att, "type", None)
            if att_type == "file":
                file_payload = getattr(att, "payload", None)
                break

        if file_payload:
            logging.info(f"Админ {user_id} прислал файл")

            file_url = (
                getattr(file_payload, "url", None)
                or getattr(file_payload, "token", None)
            )

            if not file_url and isinstance(file_payload, dict):
                file_url = (
                    file_payload.get("url")
                    or file_payload.get("token")
                )

            if not file_url:
                await send(event, "Не удалось получить ссылку на файл.")
                return

            try:
                import urllib.request
                req = urllib.request.Request(
                    file_url,
                    headers={"User-Agent": "HermesAI-Bot/1.0"},
                )
                raw = urllib.request.urlopen(req, timeout=30).read()
                text = raw.decode("utf-8")
                data = json.loads(text)
            except Exception as e:
                logging.exception("Не удалось скачать JSON")
                await send(event, f"Ошибка при чтении файла: {e}")
                return

            try:
                report = import_from_json(data)
            except Exception:
                logging.exception("Ошибка импорта")
                await send(event, "Ошибка при импорте.")
                return

            text = (
                "Импорт завершён.\n\n"
                f"Городов добавлено: {report['cities_added']}\n"
                f"Категорий добавлено: {report['categories_added']}\n"
                f"Мест добавлено: {report['places_added']}\n"
                f"Мест обновлено: {report['places_updated']}\n"
                f"Мест пропущено: {report['places_skipped']}"
            )

            if report["errors"]:
                text += "\n\nОшибки:\n"
                text += "\n".join(report["errors"][:10])
                if len(report["errors"]) > 10:
                    text += f"\n...и ещё {len(report['errors']) - 10}"

            await send(event, text, keyboard=admin_keyboard())
            return

    await _handle_ai_text(event, user_id)


# CALLBACK

@dp.message_callback()
async def callback_handler(event: MessageCallback):

    payload = event.callback.payload

    user_id = event.callback.user.user_id

    logging.info(f">>> CALLBACK {payload} user={user_id}")

    session = SessionLocal()

    try:

        # СОХРАНИТЬ МАРШРУТ

        if payload == "route:save":
            payload_data = user_last_route.get(user_id)

            if not payload_data:
                await send(
                    event,
                    "❌ Нет маршрута для сохранения.\n"
                    "Сначала постройте маршрут.",
                    keyboard=main_menu_keyboard(),
                )
                return

            try:
                route = SavedRoute(
                    user_id=user_id,
                    city_id=None,
                    city_name=payload_data.get("city_name"),
                    title=(
                        f"{payload_data.get('city_name') or 'Маршрут'} "
                        f"· {_msk_now_str()}"
                    ),
                    places=payload_data["places"],
                    total_distance=payload_data.get("total_distance", 0),
                    total_price=payload_data.get("total_price", 0),
                    places_count=len(payload_data["places"]),
                )
                session.add(route)
                session.commit()
                session.refresh(route)

                logging.info(f"Маршрут сохранён: id={route.id}, user={user_id}")

                await send(
                    event,
                    "✅ Маршрут сохранён!\n\n"
                    "Посмотреть все маршруты — Главное меню → «Мои маршруты»",
                    keyboard=after_route_keyboard(
                        map_link=payload_data.get("map_link")
                    ),
                )
            except Exception:
                session.rollback()
                logging.exception("Ошибка сохранения маршрута")
                await send(
                    event,
                    "❌ Не удалось сохранить маршрут.",
                    keyboard=main_menu_keyboard(),
                )
            return

        # МОИ МАРШРУТЫ
        if payload == "my_routes":
            routes = session.query(SavedRoute).filter(
                SavedRoute.user_id == user_id
            ).order_by(SavedRoute.created_at.desc()).limit(10).all()

            if not routes:
                await send(
                    event,
                    "У вас пока нет сохранённых маршрутов.\n\n"
                    "Постройте маршрут и нажмите «Сохранить маршрут».",
                    keyboard=main_menu_keyboard(),
                )
                return

            builder = InlineKeyboardBuilder()
            for r in routes:
                date = _fmt_msk(r.created_at)
                city_part = f"{r.city_name} · " if r.city_name else ""
                text = f"{city_part}{date} · {r.places_count} мест"

                builder.row(
                    CallbackButton(
                        text=text,
                        payload=f"show_route:{r.id}",
                    )
                )
            builder.row(
                CallbackButton(text="⬅️ Назад", payload="main_menu")
            )

            await send(
                event,
                f"📋 Ваши маршруты ({len(routes)}):\n\n"
                "Нажмите на маршрут, чтобы открыть:",
                keyboard=builder.as_markup(),
            )
            return

        # ОТКРЫТЬ МАРШРУТ
        if payload.startswith("show_route:"):
            try:
                route_id = int(payload.split(":")[1])
            except (IndexError, ValueError):
                await send(event, "❌ Ошибка.", keyboard=main_menu_keyboard())
                return

            route = session.query(SavedRoute).filter(
                SavedRoute.id == route_id,
                SavedRoute.user_id == user_id,
            ).first()

            if not route:
                await send(
                    event,
                    "❌ Маршрут не найден.",
                    keyboard=main_menu_keyboard(),
                )
                return

            date = _fmt_msk(route.created_at, "%d.%m.%Y %H:%M")

            message = f"{route.title}\n\n"
            if route.city_name:
                message += f"Город: {route.city_name}\n"
            message += f"Дата: {date}\n\n"
            message += "📍 Маршрут:\n\n"

            for index, place in enumerate(route.places, start=1):
                price = place.get("price", 0)
                price_text = "бесплатно" if price == 0 else f"{price} ₽"

                message += (
                    f"{index}. {place.get('name', '—')}\n"
                    f"📌 {place.get('address', '—')}\n"
                    f"💰 {price_text}\n"
                )
                if place.get("opening_hours"):
                    message += f"⏰ {place['opening_hours']}\n"
                if place.get("description"):
                    message += f"ℹ️ {place['description']}\n"
                message += "\n"

            message += (
                "━━━━━━━━━━━━━━\n"
                f"🚶 Расстояние: {route.total_distance or 0:.1f} км\n"
                f"💰 Стоимость: {route.total_price or 0} ₽\n"
                f"📍 Мест: {route.places_count or 0}"
            )

            builder = InlineKeyboardBuilder()
            builder.row(
                CallbackButton(
                    text="🗑 Удалить маршрут",
                    payload=f"delete_route:{route.id}",
                )
            )
            builder.row(
                CallbackButton(text="⬅️ К списку", payload="my_routes"),
                CallbackButton(text="🏠 Меню", payload="main_menu"),
            )

            await send(event, message, keyboard=builder.as_markup())
            return

        # УДАЛИТЬ МАРШРУТ
        if payload.startswith("delete_route:"):
            try:
                route_id = int(payload.split(":")[1])
            except (IndexError, ValueError):
                await send(event, "❌ Ошибка.", keyboard=main_menu_keyboard())
                return

            route = session.query(SavedRoute).filter(
                SavedRoute.id == route_id,
                SavedRoute.user_id == user_id,
            ).first()

            if not route:
                await send(
                    event,
                    "❌ Маршрут не найден.",
                    keyboard=main_menu_keyboard(),
                )
                return

            session.delete(route)
            session.commit()

            logging.info(f"Маршрут удалён: id={route_id}")

            await send(
                event,
                "🗑 Маршрут удалён.",
                keyboard=main_menu_keyboard(),
            )
            return

        # АДМИН: шаблон и инструкция

        if payload == "admin:template":
            if not _is_admin(user_id):
                await send(event, "Нет доступа.")
                return

            template_path = Path("data/template.json")
            instructions_path = Path("data/instructions.txt")

            if not template_path.exists():
                await send(event, "Шаблон не найден.")
                return

            if not instructions_path.exists():
                await send(event, "Инструкция не найдена.")
                return

            try:
                instr_media = InputMedia(path=str(instructions_path))
                await event.message.answer(
                    "Инструкция по заполнению JSON:",
                    attachments=[instr_media],
                )
            except Exception:
                logging.exception("Не удалось отправить инструкцию")
                return

            try:
                tpl_media = InputMedia(path=str(template_path))
                await event.message.answer(
                    "Шаблон JSON. Заполни и пришли обратно:",
                    attachments=[tpl_media],
                )
            except Exception:
                logging.exception("Не удалось отправить шаблон")
                return

            return

        # ВЫБОР ГОРОДА

        if payload.startswith("city:"):
            value = payload.split(":")[1]

            if value == "other":
                await send(
                    event,
                    "Другие города появятся позже.\n\n"
                    "Выбери один из доступных:",
                    keyboard=city_keyboard(session, back_payload="main_menu"),
                )
                return

            city_id = int(value)
            user = _get_user(session, user_id)
            if not user:
                user = User(max_user_id=user_id)
                session.add(user)

            user.city_id = city_id
            session.commit()

            user_route_settings[user_id] = _default_settings()

            await _send_main_menu(
                event,
                _city_name(session, city_id),
                user_id=user_id,
                city_id=city_id,
            )
            return

        # СМЕНИТЬ ГОРОД

        if payload == "change_city":
            await _send_city_choice(session, event, back_payload="main_menu")
            return

        # ГЛАВНОЕ МЕНЮ

        if payload == "main_menu":
            user = _get_user(session, user_id)

            if not user or not user.city_id:
                await send(
                    event,
                    "Сначала выбери город:",
                    keyboard=city_keyboard(session),
                )
                return

            user_route_settings[user_id] = _default_settings()

            await _send_main_menu(
                event,
                _city_name(session, user.city_id),
                user_id=user_id,
                city_id=user.city_id,
            )
            return

        # ПОСТРОИТЬ МАРШРУТ

        if payload == "build_route":
            user_route_settings[user_id] = _default_settings()
            await send(
                event,
                "Начинаем создавать маршрут!\n\n"
                "На сколько времени планируем прогулку?",
                keyboard=time_keyboard(),
            )
            return

        # ВРЕМЯ

        if payload.startswith("time:"):
            hours = int(payload.split(":")[1])

            settings = user_route_settings.get(user_id, {})

            if settings.get("ai_mode"):
                text = settings.get("ai_text")
                params = settings.get("ai_params", {})
                params["time"] = hours

                if not params.get("budget"):
                    params["budget"] = None
                if not params.get("category_ids"):
                    params["category_ids"] = [1, 2, 3, 4, 5, 6]

                await _build_ai_route(event, session, user_id, text, params)
                user_route_settings.pop(user_id, None)
                return

            settings = _get_or_create_settings(user_id)
            settings["time"] = hours

            await send(
                event,
                f"Отлично! Маршрут примерно на {hours} ч.\n\n"
                "💰 Теперь выбери бюджет:",
                keyboard=budget_keyboard(),
            )
            return

        # БЮДЖЕТ

        if payload.startswith("budget:"):
            value = payload.split(":")[1]

            if value == "none":
                budget = None
                budget_text = "без ограничений"
            else:
                budget = int(value)
                budget_text = (
                    "бесплатный маршрут" if budget == 0
                    else f"до {budget} ₽"
                )

            settings = _get_or_create_settings(user_id)
            settings["budget"] = budget

            categories = session.query(Category).order_by(Category.id).all()

            if not categories:
                await send(
                    event,
                    "❌ В базе данных пока нет категорий.",
                    keyboard=main_menu_keyboard(),
                )
                return

            await send(
                event,
                "❤️ Теперь выбери интересы.\n\n"
                f"💰 Бюджет: {budget_text}\n\n"
                "Можно выбрать несколько категорий:",
                keyboard=category_keyboard(
                    categories,
                    settings["categories"],
                ),
            )
            return

        # НАЗАД К БЮДЖЕТУ

        if payload == "back_to_budget":
            settings = _get_or_create_settings(user_id)

            budget = settings.get("budget")
            budget_text = (
                "не выбран"
                if budget is None
                else (
                    "бесплатный маршрут" if budget == 0
                    else f"до {budget} ₽"
                )
            )

            await send(
                event,
                "Выбери бюджет:\n\n"
                f"Текущий выбор: {budget_text}",
                keyboard=budget_keyboard(),
            )
            return

        # КАТЕГОРИИ

        if payload.startswith("category:"):
            category_id = int(payload.split(":")[1])

            settings = _get_or_create_settings(user_id)
            selected = settings["categories"]

            if category_id in selected:
                selected.remove(category_id)
            else:
                selected.append(category_id)

            categories = session.query(Category).order_by(Category.id).all()

            keyboard = category_keyboard(categories, selected)

            try:
                await event.message.edit(
                    text="❤️ Выберите интересующие категории.\n\n"
                        "Можно выбрать несколько вариантов 👇",
                    attachments=[keyboard],
                )
            except Exception:
                logging.exception("Не удалось обновить меню категорий")

            return

        # PDF

        if payload == "route:pdf":
            payload_data = user_last_route.get(user_id)

            if not payload_data:
                await send(
                    event,
                    "❌ Нет сохранённого маршрута.\n"
                    "Сначала постройте маршрут.",
                    keyboard=main_menu_keyboard(),
                )
                return

            try:
                pdf_path = generate_route_pdf(payload_data)
                logging.info(f"PDF сгенерирован: {pdf_path}")
            except Exception:
                logging.exception("ОШИБКА при генерации PDF")
                await send(
                    event,
                    "❌ Не удалось создать PDF.",
                    keyboard=after_route_keyboard(),
                )
                return

            try:
                await send_pdf(event, pdf_path)
            except Exception:
                logging.exception("ОШИБКА при отправке PDF")
                await send(
                    event,
                    "❌ Не удалось отправить PDF.",
                    keyboard=after_route_keyboard(),
                )
                return

            await send(
                event,
                "PDF с маршрутом готов.\n"
                "Его можно переслать в MAX другому человеку.",
                keyboard=after_pdf_keyboard(),
            )
            return

        # АДМИНКА

        if payload == "admin:db":
            if not _is_admin(user_id):
                return
            await send(
                event,
                "📊 База данных\n\nВыбери раздел:",
                keyboard=admin_db_keyboard(),
            )
            return

        if payload == "admin:import":
            if not _is_admin(user_id):
                return
            await send(
                event,
                "📥 Импорт JSON\n\n"
                "Скачай шаблон, заполни и отправь файл .json "
                "в этот чат. Бот добавит данные в БД.",
                keyboard=admin_import_keyboard(),
            )
            return

        if payload == "admin:back":
            if not _is_admin(user_id):
                return
            await send(
                event,
                "Админ-панель\n\nВыбери раздел:",
                keyboard=admin_keyboard(),
            )
            return

        if payload == "admin:export":
            if not _is_admin(user_id):
                return

            await send(event, "📤 Готовлю экспорт всей БД...")

            try:
                export_path = export_db_to_json()
            except Exception:
                logging.exception("ОШИБКА при экспорте БД")
                await send(
                    event,
                    "❌ Не удалось создать экспорт.",
                    keyboard=admin_keyboard(),
                )
                return

            try:
                media = InputMedia(path=str(export_path))
                await event.message.answer(
                    "📤 Экспорт всей БД готов.\n\n"
                    "Файл совместим с импортом — "
                    "можно загрузить обратно через «📥 Импорт JSON».",
                    attachments=[media],
                )
            except Exception:
                logging.exception("Не удалось отправить экспорт")
                await send(
                    event,
                    f"❌ Не удалось отправить файл.\n"
                    f"Путь: {export_path}",
                    keyboard=admin_keyboard(),
                )
                return

            await send(event, "Что дальше?", keyboard=admin_keyboard())
            return

        if payload == "admin:export_city_menu":
            if not _is_admin(user_id):
                return
            await send(
                event,
                "📤 Выбери город для экспорта:",
                keyboard=admin_export_city_keyboard(session),
            )
            return

        if payload.startswith("admin:export_city:"):
            if not _is_admin(user_id):
                return

            try:
                city_id = int(payload.split(":")[2])
            except (IndexError, ValueError):
                await send(event, "❌ Ошибка в payload.", keyboard=admin_keyboard())
                return

            await send(event, "📤 Готовлю экспорт города...")

            try:
                export_path = export_city_to_json(city_id)
            except ValueError as e:
                await send(event, f"❌ {e}", keyboard=admin_keyboard())
                return
            except Exception:
                logging.exception("ОШИБКА при экспорте города")
                await send(
                    event,
                    "❌ Не удалось создать экспорт.",
                    keyboard=admin_keyboard(),
                )
                return

            try:
                media = InputMedia(path=str(export_path))
                await event.message.answer(
                    "📤 Экспорт города готов.\n\n"
                    "Файл совместим с импортом.",
                    attachments=[media],
                )
            except Exception:
                logging.exception("Не удалось отправить экспорт")
                await send(
                    event,
                    f"❌ Не удалось отправить файл.\n"
                    f"Путь: {export_path}",
                    keyboard=admin_keyboard(),
                )
                return

            await send(
                event,
                "📤 Выбери город для экспорта:",
                keyboard=admin_export_city_keyboard(session),
            )
            return

        if payload == "admin:no_coords":
            if not _is_admin(user_id):
                return

            places = session.query(Place).filter(
                (Place.latitude.is_(None)) | (Place.longitude.is_(None))
            ).order_by(Place.city_id, Place.name).all()

            if not places:
                await send(
                    event,
                    "✅ Все места имеют координаты.",
                    keyboard=admin_db_keyboard(),
                )
                return

            from collections import defaultdict
            by_city = defaultdict(list)

            cities_map = {
                c.id: c.name for c in session.query(City).all()
            }

            for p in places:
                city_name = cities_map.get(p.city_id, f"id={p.city_id}")
                by_city[city_name].append(p)

            total = len(places)

            lines = [
                f"Места без координат: {total}\n",
                "Эти места бот НЕ включает в маршруты.\n",
            ]

            LIMIT = 30
            shown = 0

            for city_name, city_places in sorted(by_city.items()):
                lines.append(f"\n{city_name}: {len(city_places)}")
                for p in city_places:
                    if shown >= LIMIT:
                        break
                    lines.append(f"   • id={p.id}: {p.name}")
                    shown += 1
                if shown >= LIMIT:
                    break

            if total > LIMIT:
                lines.append(
                    f"\n...и ещё {total - LIMIT} мест. "
                    f"Показаны первые {LIMIT}."
                )

            await send(event, "\n".join(lines), keyboard=admin_db_keyboard())
            return

        if payload == "admin:stats":
            if not _is_admin(user_id):
                return

            cities_count = session.query(City).count()
            categories_count = session.query(Category).count()
            places_total = session.query(Place).count()
            places_active = session.query(Place).filter(
                Place.is_active == True
            ).count()
            users_total = session.query(User).count()

            text = (
                "📊 Статистика базы данных\n\n"
                f"🏙️ Городов: {cities_count}\n"
                f"📂 Категорий: {categories_count}\n"
                f"📍 Мест всего: {places_total}\n"
                f"✅ Мест активных: {places_active}\n"
                f"👥 Пользователей: {users_total}\n"
            )

            await send(event, text, keyboard=admin_db_keyboard())
            return

        if payload == "admin:cities":
            if not _is_admin(user_id):
                return

            cities = session.query(City).order_by(City.name).all()

            if not cities:
                await send(
                    event,
                    "Городов нет в базе данных.",
                    keyboard=admin_db_keyboard(),
                )
                return

            lines = ["🏙️ Города в базе данных\n"]

            for city in cities:
                total = session.query(Place).filter(
                    Place.city_id == city.id
                ).count()
                active = session.query(Place).filter(
                    Place.city_id == city.id,
                    Place.is_active == True,
                ).count()

                lines.append(
                    f"• {city.name} (id={city.id})\n"
                    f"  мест: {active} активных / {total} всего"
                )

            await send(event, "\n".join(lines), keyboard=admin_db_keyboard())
            return

        if payload == "admin:categories":
            if not _is_admin(user_id):
                return

            categories = session.query(Category).order_by(Category.id).all()

            if not categories:
                await send(
                    event,
                    "Категорий нет в базе данных.",
                    keyboard=admin_db_keyboard(),
                )
                return

            lines = ["📂 Категории\n"]

            for cat in categories:
                active = session.query(Place).filter(
                    Place.category_id == cat.id,
                    Place.is_active == True,
                ).count()
                total = session.query(Place).filter(
                    Place.category_id == cat.id
                ).count()

                lines.append(
                    f"• {cat.id}. {cat.name}\n"
                    f"  мест: {active} активных / {total} всего"
                )

            await send(event, "\n".join(lines), keyboard=admin_db_keyboard())
            return

        # ГЕНЕРАЦИЯ МАРШРУТА (обычный режим)

        if payload == "route:generate":
            settings = user_route_settings.get(user_id)

            if not settings:
                await send(
                    event,
                    "❌ Сначала начните создание маршрута.",
                    keyboard=main_menu_keyboard(),
                )
                return

            hours = settings["time"]
            budget = settings["budget"]
            category_ids = settings["categories"]

            if hours is None:
                await send(
                    event,
                    "⚠️ Сначала выберите время маршрута.",
                    keyboard=time_keyboard(),
                )
                return

            if not category_ids:
                await send(
                    event,
                    "⚠️ Выберите хотя бы одну категорию.",
                    keyboard=category_keyboard(
                        session.query(Category).order_by(Category.id).all(),
                        [],
                    ),
                )
                return

            user = _get_user(session, user_id)

            if not user or not user.city_id:
                await send(
                    event,
                    "❌ Сначала выберите город.",
                    keyboard=city_keyboard(session),
                )
                return

            places = session.query(Place).filter(
                Place.city_id == user.city_id,
                Place.is_active == True,
            ).all()

            logging.info(f"Получено мест из БД: {len(places)}")

            if not places:
                await send(
                    event,
                    "😔 В этом городе пока нет мест.",
                    keyboard=main_menu_keyboard(),
                )
                return

            city = session.query(City).filter(City.id == user.city_id).first()
            places, reasons, weather = _apply_context_filters(session, places, city)

            route = None
            try:
                route = build_route(
                    places,
                    min_places=3,
                    max_places=8,
                    max_budget=budget,
                    max_time_hours=hours,
                    category_ids=category_ids,
                )
                logging.info(f"build_route вернул: {len(route)} мест")
            except Exception:
                logging.exception("ОШИБКА в build_route")
                route = None

            if not route:
                await send(
                    event,
                    "😔 Не удалось найти подходящий маршрут.\n\n"
                    "Попробуйте:\n"
                    "• увеличить бюджет;\n"
                    "• выбрать больше категорий;\n"
                    "• увеличить время прогулки.",
                    keyboard=main_menu_keyboard(),
                )
                return

            try:
                route_data = [_serialize_place(p) for p in route]
            except Exception:
                logging.exception("ОШИБКА при сериализации route")
                route_data = []

            if not route_data:
                logging.warning("route_data пустой")
                return

            try:
                total_distance = calculate_route_distance(route)
            except Exception:
                logging.exception("ОШИБКА в calculate_route_distance")
                total_distance = 0.0

            total_price = sum(item["price"] for item in route_data)

            try:
                selected_categories = session.query(Category).filter(
                    Category.id.in_(category_ids)
                ).all()
                category_names = [
                    _safe_str(c.name) for c in selected_categories if c.name
                ]
            except Exception:
                logging.exception("ОШИБКА при получении категорий")
                category_names = []

            message = _format_route_message(
                route_data=route_data,
                total_distance=total_distance,
                total_price=total_price,
                hours=hours,
                budget=budget,
                category_names=category_names,
                weather=weather,
                reasons=reasons,
                city_name=_city_name(session, city.id),
                text=None,
            )

            map_path = None
            try:
                import uuid as _uuid
                map_id = _uuid.uuid4().hex[:8]
                map_path = generate_route_map(route_data, route_id=map_id)
                if map_path:
                    media = InputMedia(path=str(map_path))
                    await event.message.answer(
                        "Карта маршрута:",
                        attachments=[media],
                    )
            except Exception:
                logging.exception("Не удалось отправить карту")

            map_link = None
            try:
                map_link = generate_yandex_maps_link(route_data)
            except Exception:
                logging.exception("Не удалось создать ссылку")

            user_last_route[user_id] = {
                "city_name": _city_name(session, user.city_id),
                "hours": hours,
                "budget_text": (
                    "без ограничений" if budget is None
                    else f"до {budget} ₽"
                ),
                "category_names": category_names,
                "places": route_data,
                "total_distance": total_distance,
                "total_price": total_price,
                "map_path": str(map_path) if map_path else None,
                "map_link": map_link,
            }

            await send(
                event,
                message,
                keyboard=after_route_keyboard(map_link=map_link),
            )

            user_route_settings.pop(user_id, None)
            logging.info(f"<<< CALLBACK {payload} user={user_id}")
            return

        logging.warning(f"Неизвестный callback: {payload}")

    except Exception:
        logging.exception("Ошибка callback")

    finally:
        try:
            session.rollback()
        except Exception:
            logging.exception("rollback failed")
        try:
            session.close()
        except Exception:
            logging.exception("close failed")


