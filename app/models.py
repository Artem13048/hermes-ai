"""
Скрипт с моделями/классами для упрощения работы с SQLAlchemy
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    BigInteger,
    Column,
    DateTime,
    UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# ============================================================
# CITY
# ============================================================

class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    region: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    country: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="Россия"
    )

    latitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    longitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    places: Mapped[list["Place"]] = relationship(
        "Place",
        back_populates="city"
    )

    routes: Mapped[list["Route"]] = relationship(
        "Route",
        back_populates="city"
    )

    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="city"
    )


# ============================================================
# CATEGORY
# ============================================================

class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False
    )

    places: Mapped[list["Place"]] = relationship(
        "Place",
        back_populates="category"
    )


# ============================================================
# PLACE
# ============================================================

class Place(Base):
    __tablename__ = "places"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    city_id: Mapped[int] = mapped_column(
        ForeignKey(
            "cities.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey(
            "categories.id",
        ),
        nullable=False
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    address: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    long_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    price: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default="0"
    )

    ticket_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    source_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    photo_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    latitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    longitude: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    rating: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    opening_hours: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    benefits: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    city: Mapped["City"] = relationship(
        "City",
        back_populates="places"
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="places"
    )

    route_places: Mapped[list["RoutePlace"]] = relationship(
        "RoutePlace",
        back_populates="place"
    )


# ============================================================
# USER
# ============================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    max_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False
    )

    city_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "cities.id",
            ondelete="SET NULL"
        ),
        nullable=True
    )

    city: Mapped["City | None"] = relationship(
        "City",
        back_populates="users"
    )

    routes: Mapped[list["Route"]] = relationship(
        "Route",
        back_populates="user"
    )


# ============================================================
# ROUTE
# ============================================================

class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE"
        ),
        nullable=True
    )

    city_id: Mapped[int] = mapped_column(
        ForeignKey(
            "cities.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    total_distance: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    total_time: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    total_price: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="routes"
    )

    city: Mapped["City"] = relationship(
        "City",
        back_populates="routes"
    )

    places: Mapped[list["RoutePlace"]] = relationship(
        "RoutePlace",
        back_populates="route",
        order_by="RoutePlace.position"
    )


# ============================================================
# ROUTE PLACE
# ============================================================

class RoutePlace(Base):
    __tablename__ = "route_places"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    route_id: Mapped[int] = mapped_column(
        ForeignKey(
            "routes.id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    place_id: Mapped[int] = mapped_column(
        ForeignKey(
            "places.id",
            ondelete="RESTRICT"
        ),
        nullable=False
    )

    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    travel_time_from_previous: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    distance_from_previous: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    route: Mapped["Route"] = relationship(
        "Route",
        back_populates="places"
    )

    place: Mapped["Place"] = relationship(
        "Place",
        back_populates="route_places"
    )


# ============================================================
# USER LIKE
# ============================================================

class UserLike(Base):
    __tablename__ = "user_likes"

    id = Column(
        Integer,
        primary_key=True
    )

    max_user_id = Column(
        BigInteger,
        nullable=False,
        index=True
    )

    place_id = Column(
        Integer,
        ForeignKey("places.id"),
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "max_user_id",
            "place_id",
            name="uq_user_place",
        ),
    )


# ============================================================
# SAVED ROUTE
# ============================================================

class SavedRoute(Base):
    __tablename__ = "saved_routes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True
    )

    city_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    city_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    places: Mapped[list] = mapped_column(
        JSONB,
        nullable=False
    )

    total_distance: Mapped[float | None] = mapped_column(
        Float,
        nullable=True
    )

    total_price: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    places_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )