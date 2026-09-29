"""
HTTP API для мини-приложения
"""

import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import Place, City, Category, UserLike


logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Hermes.AI Mini App API")

# CORS — чтобы мини-приложение могло обращаться к API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# СХЕМЫ
# ============================================================

class LikeRequest(BaseModel):
    user_id: int
    place_id: int


# ============================================================
# ЭНДПОИНТЫ
# ============================================================

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
    """Список категорий для табов."""
    session = SessionLocal()
    try:
        cats = session.query(Category).order_by(Category.id).all()
        return [{"id": c.id, "name": c.name} for c in cats]
    finally:
        session.close()


@app.get("/api/places")
def get_places(
    city_id: int,
    category_id: Optional[int] = None,
    limit: int = 200,
):
    """
    Список мест для карточек.
    Если category_id указан — только этой категории.
    """
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
            }
            for p in places
        ]
    finally:
        session.close()


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