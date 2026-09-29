"""
HTTP API для мини-приложения Hermes.AI.
Отдаёт данные о местах, категориях, лайках, маршрутах.
Также принимает webhook от MAX Bot API через FastAPIMaxWebhook.
"""

import logging
import os
from typing import List, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Place, City, Category, UserLike


# ИМПОРТ ЯДРА БОТА (общий bot и dp с bot.py)


from app.bot_core import bot, dp

# Импортируем bot.py под другим именем, чтобы не перезаписать bot.
# Это нужно, чтобы зарегистрировались все @dp.message_created и @dp.message_callback.
import bot as bot_handlers  # noqa: F401

# FastAPIMaxWebhook — готовый класс для интеграции webhook в FastAPI
from maxapi.webhook.fastapi import FastAPIMaxWebhook



# НАСТРОЙКА


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True,
)

MSK = ZoneInfo("Europe/Moscow")

WEBHOOK_PATH = "/webhook"


def _to_msk(dt):
    """Приводит naive-UTC или aware-datetime к времени в MSK."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)
  

def _msk_now_str(fmt: str = "%d.%m %H:%M") -> str:
    """Текущее время в MSK в виде строки."""
    return datetime.now(MSK).strftime(fmt)



# СОЗДАНИЕ ПРИЛОЖЕНИЯ И WEBHOOK


# Создаём webhook-обёртку. Она сама знает, как передавать события в dp.
WEBHOOK_SECRET = os.getenv("MAX_WEBHOOK_SECRET", "")

webhook = FastAPIMaxWebhook(
    dp=dp,
    bot=bot,
    secret=WEBHOOK_SECRET,
)

# Создаём FastAPI с lifespan от webhook.
# lifespan нужен, чтобы webhook правильно инициализировался и завершался.
app = FastAPI(
    title="Hermes.AI Mini App API",
    lifespan=webhook.lifespan,
)

# Подключаем CORS для мини-приложения
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Регистрируем эндпоинт /webhook — FastAPIMaxWebhook сам обработает запросы
webhook.setup(app, path=WEBHOOK_PATH)


# СХЕМЫ

class LikeRequest(BaseModel):
    user_id: int
    place_id: int


class SaveRouteRequest(BaseModel):
    user_id: int
    city_id: Optional[int] = None
    city_name: Optional[str] = None
    title: Optional[str] = None
    places: List[dict]
    total_distance: float = 0.0
    total_price: int = 0


class GenerateRouteRequest(BaseModel):
    user_id: int
    place_ids: List[int]


class SetUserCityRequest(BaseModel):
    user_id: int
    city_id: int


# БАЗОВЫЕ

@app.get("/")
def root():
    return {"status": "ok", "service": "Hermes.AI API"}


@app.get("/api/cities")
def get_cities():
    """Список городов."""
    session = SessionLocal()
    try:
        cities = session.query(City).order_by(City.name).all()
        return [{"id": c.id, "name": c.name} for c in cities]
    finally:
        session.close()


@app.get("/api/categories")
def get_categories():
    """Список категорий."""
    session = SessionLocal()
    try:
        cats = session.query(Category).order_by(Category.id).all()
        return [{"id": c.id, "name": c.name} for c in cats]
    finally:
        session.close()


@app.get("/api/user_city")
def get_user_city(user_id: int):
    """Возвращает выбранный город пользователя."""
    session = SessionLocal()
    try:
        from app.models import User
        user = session.query(User).filter(
            User.max_user_id == user_id
        ).first()
        if not user:
            return {"user_id": user_id, "city_id": None}
        return {
            "user_id": user_id,
            "city_id": user.city_id,
        }
    finally:
        session.close()


@app.post("/api/user_city")
def set_user_city(req: SetUserCityRequest):
    """Сохраняет выбранный город пользователя."""
    session = SessionLocal()
    try:
        from app.models import User

        user = session.query(User).filter(
            User.max_user_id == req.user_id
        ).first()

        if not user:
            user = User(max_user_id=req.user_id)
            session.add(user)

        user.city_id = req.city_id
        session.commit()

        logging.info(
            f"Город пользователя обновлён: user={req.user_id}, city={req.city_id}"
        )
        return {"status": "ok", "city_id": req.city_id}
    except Exception as e:
        session.rollback()
        logging.exception("Ошибка сохранения города")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# МЕСТА


@app.get("/api/places")
def get_places(
    city_id: int,
    category_id: Optional[int] = None,
    limit: int = 200,
):
    """Список мест для карточек."""
    session = SessionLocal()
    try:
        query = session.query(Place).filter(
            Place.city_id == city_id,
            Place.is_active == True,
        )
        if category_id is not None:
            query = query.filter(Place.category_id == category_id)

        places = query.limit(limit).all()

        return [
            {
                "id": p.id,
                "name": p.name,
                "address": p.address,
                "description": p.description,
                "price": p.price or 0,
                "category_id": p.category_id,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "ticket_url": p.ticket_url,
                "source_url": p.source_url,
                "photo_url": p.photo_url,
                "opening_hours": p.opening_hours,
            }
            for p in places
        ]
    finally:
        session.close()

# ЛАЙКИ

@app.post("/api/likes")
def add_like(req: LikeRequest):
    """Сохранить лайк."""
    session = SessionLocal()
    try:
        existing = session.query(UserLike).filter(
            UserLike.max_user_id == req.user_id,
            UserLike.place_id == req.place_id,
        ).first()

        if existing:
            return {"status": "already_liked"}

        like = UserLike(
            max_user_id=req.user_id,
            place_id=req.place_id,
        )
        session.add(like)
        session.commit()
        return {"status": "ok", "place_id": req.place_id}
    finally:
        session.close()


@app.delete("/api/likes")
def remove_like(req: LikeRequest):
    """Убрать лайк."""
    session = SessionLocal()
    try:
        like = session.query(UserLike).filter(
            UserLike.max_user_id == req.user_id,
            UserLike.place_id == req.place_id,
        ).first()

        if like:
            session.delete(like)
            session.commit()
            return {"status": "removed"}
        return {"status": "not_found"}
    finally:
        session.close()


@app.get("/api/likes")
def get_likes(user_id: int):
    """Все лайки пользователя."""
    session = SessionLocal()
    try:
        likes = session.query(UserLike).filter(
            UserLike.max_user_id == user_id
        ).all()
        return {
            "user_id": user_id,
            "place_ids": [lk.place_id for lk in likes],
            "count": len(likes),
        }
    finally:
        session.close()


# ГЕНЕРАЦИЯ МАРШРУТА

@app.post("/api/route/generate")
def generate_route(req: GenerateRouteRequest):
    """Строит маршрут из указанных мест."""
    session = SessionLocal()
    try:
        from services.route_service import calculate_route_distance

        places = session.query(Place).filter(
            Place.id.in_(req.place_ids),
            Place.is_active == True,
        ).all()

        if not places:
            return {
                "status": "error",
                "message": "Места не найдены",
            }

        place_map = {p.id: p for p in places}
        ordered_places = [
            place_map[pid] for pid in req.place_ids
            if pid in place_map
        ]

        total_distance = calculate_route_distance(ordered_places)
        total_price = sum(int(p.price or 0) for p in ordered_places)

        route_data = [
            {
                "id": p.id,
                "name": p.name,
                "address": p.address,
                "description": p.description,
                "price": int(p.price or 0),
                "latitude": p.latitude,
                "longitude": p.longitude,
                "opening_hours": p.opening_hours,
                "ticket_url": p.ticket_url,
                "source_url": p.source_url,
                "photo_url": p.photo_url,
            }
            for p in ordered_places
        ]

        logging.info(
            f"Маршрут сгенерирован: {len(route_data)} мест, "
            f"{total_distance:.1f} км, {total_price} ₽"
        )

        return {
            "status": "ok",
            "route": route_data,
            "total_distance": round(total_distance, 1),
            "total_price": total_price,
            "places_count": len(route_data),
        }

    except Exception as e:
        logging.exception("Ошибка генерации маршрута")
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


# СОХРАНЁННЫЕ МАРШРУТЫ

@app.post("/api/routes/save")
def save_route(req: SaveRouteRequest):
    """Сохраняет маршрут в историю пользователя."""
    session = SessionLocal()
    try:
        from app.models import SavedRoute

        route = SavedRoute(
            user_id=req.user_id,
            city_id=req.city_id,
            city_name=req.city_name,
            title=req.title or f"Маршрут от {_msk_now_str()}",
            places=req.places,
            total_distance=req.total_distance,
            total_price=req.total_price,
            places_count=len(req.places),
        )
        session.add(route)
        session.commit()
        session.refresh(route)

        logging.info(
            f"Маршрут сохранён: id={route.id}, user={req.user_id}"
        )
        return {"status": "ok", "route_id": route.id}

    except Exception as e:
        session.rollback()
        logging.exception("Ошибка сохранения маршрута")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/api/routes/list")
def list_routes(user_id: int, limit: int = 50):
    """Список сохранённых маршрутов пользователя."""
    session = SessionLocal()
    try:
        from app.models import SavedRoute

        routes = session.query(SavedRoute).filter(
            SavedRoute.user_id == user_id
        ).order_by(SavedRoute.created_at.desc()).limit(limit).all()

        return [
            {
                "id": r.id,
                "title": r.title,
                "city_name": r.city_name,
                "places_count": r.places_count,
                "total_distance": r.total_distance,
                "total_price": r.total_price,
                "created_at": (
                    _to_msk(r.created_at).isoformat()
                    if r.created_at else None
                ),
            }
            for r in routes
        ]
    finally:
        session.close()


@app.get("/api/routes/{route_id}")
def get_route(route_id: int):
    """Один маршрут по id."""
    session = SessionLocal()
    try:
        from app.models import SavedRoute

        route = session.query(SavedRoute).filter(
            SavedRoute.id == route_id
        ).first()

        if not route:
            raise HTTPException(
                status_code=404,
                detail="Маршрут не найден",
            )

        return {
            "id": route.id,
            "user_id": route.user_id,
            "city_name": route.city_name,
            "title": route.title,
            "places": route.places,
            "total_distance": route.total_distance,
            "total_price": route.total_price,
            "places_count": route.places_count,
            "created_at": (
                _to_msk(route.created_at).isoformat()
                if route.created_at else None
            ),
        }
    finally:
        session.close()


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: int):
    """Удаляет сохранённый маршрут."""
    session = SessionLocal()
    try:
        from app.models import SavedRoute

        route = session.query(SavedRoute).filter(
            SavedRoute.id == route_id
        ).first()

        if not route:
            raise HTTPException(
                status_code=404,
                detail="Маршрут не найден",
            )

        session.delete(route)
        session.commit()

        logging.info(f"Маршрут удалён: id={route_id}")
        return {"status": "removed", "route_id": route_id}
    finally:
        session.close()


# ЗАПУСК

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", 8000)),
        log_level="info",
    )