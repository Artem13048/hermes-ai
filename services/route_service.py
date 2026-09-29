import random
from math import radians, sin, cos, sqrt, atan2


# работа с координатами

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1 = radians(lat1), radians(lon1)
    lat2, lon2 = radians(lat2), radians(lon2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def calculate_route_distance(route):
    if not route or len(route) < 2:
        return 0.0
    total = 0.0
    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        total += calculate_distance(
            a.latitude, a.longitude,
            b.latitude, b.longitude,
        )
    return total


def _safe_price(place) -> int:
    # извлекаем цену места
    try:
        return int(place.price or 0)
    except (TypeError, ValueError):
        return 0


def calculate_route_price(route):
    if not route:
        return 0
    return sum(_safe_price(p) for p in route)


def estimate_route_hours(route, speed_kmh=4.0, visit_hours=0.5):
    walking = calculate_route_distance(route) / speed_kmh
    visiting = len(route) * visit_hours
    return walking + visiting


# ВСПОМОГАТЕЛЬНЫЕ

def _trim_by_time(route, max_time_hours, min_places):
    while (
        len(route) > min_places
        and estimate_route_hours(route) > max_time_hours
    ):
        route.pop()
    return route


def _trim_by_budget(route, max_budget, min_places):
    # работа с общей ценой и проверка лимита бюджета
    while (
        len(route) > min_places
        and calculate_route_price(route) > max_budget
    ):
        route.pop()

    # уменьшение до 1 места если не влезает в бюджет
    while (
        len(route) > 1
        and calculate_route_price(route) > max_budget
    ):
        route.pop()

    return route


'''
Основная логика
'''

def build_route(
    places,
    min_places=5,
    max_places=8,
    max_budget=None,
    max_time_hours=None,
    category_ids=None,
):
    """
    Генерирует маршрут.

    Режимы:
    - max_budget=None   → самый ДОРОГОЙ маршрут (сначала дорогие места)
    - max_budget=0      → только бесплатные места
    - max_budget>0      → уложиться в бюджет, сначала дорогие,
                          но не превышающие остаток
    """

    # --- 1. Только места с координатами ---
    places = [
        p for p in places
        if getattr(p, "latitude", None) is not None
        and getattr(p, "longitude", None) is not None
    ]

    # --- 2. Фильтр по категориям ---
    if category_ids:
        cset = set(category_ids)
        places = [
            p for p in places
            if getattr(p, "category_id", None) in cset
        ]

    if not places:
        return []

    # --- 3. Фильтр по бюджету на входе ---
    # max_budget=0     → только бесплатные
    # max_budget>0     → каждое место <= бюджета
    # max_budget=None  → не фильтруем (нужен самый дорогой маршрут)
    if max_budget is not None:
        places = [p for p in places if _safe_price(p) <= max_budget]

    if not places:
        return []

    # --- 4. Если мест мало — отдаём как есть ---
    if len(places) <= min_places:
        route = list(places)
        if max_time_hours is not None:
            route = _trim_by_time(route, max_time_hours, min_places=1)
        if max_budget is not None:
            route = _trim_by_budget(route, max_budget, min_places=1)
        return route

    # 5. Размер маршрута
    route_size = random.randint(
        min_places,
        min(max_places, len(places)),
    )

    # 6. Стартовая точка
    #   budget=None  → начинаем с самого дорогого
    #   budget задан → случайная (или тоже с дорогого — решай)
    if max_budget is None:
        start = max(places, key=_safe_price)
    else:
        start = random.choice(places)

    route = [start]

    start_id = getattr(start, "id", None)
    if start_id is not None:
        remaining = [
            p for p in places
            if getattr(p, "id", None) != start_id
        ]
    else:
        remaining = [p for p in places if p is not start]

    # 7. Жадный выбор
    while remaining and len(route) < route_size:

        current = route[-1]

        current_price = calculate_route_price(route)
        remaining_budget = (
            max_budget - current_price
            if max_budget is not None
            else None
        )

        candidates_pool = []
        for place in remaining:
            place_price = _safe_price(place)

            # Пропускаем места, которые не влезают в остаток бюджета
            if (
                remaining_budget is not None
                and place_price > remaining_budget
            ):
                continue

            distance = calculate_distance(
                current.latitude, current.longitude,
                place.latitude, place.longitude,
            )
            candidates_pool.append((distance, place_price, place))

        if not candidates_pool:
            break

        # Смотрим, какие категории уже есть в маршруте
        used_categories = {
            getattr(p, "category_id", None) for p in route
        }

        # Приоритет: места из "новых" категорий
        prefer_new_category = []

        for item in candidates_pool:
            distance, price, place = item
            cat_id = getattr(place, "category_id", None)
            is_new = cat_id not in used_categories
            prefer_new_category.append((is_new, distance, price, place))

        # Сортируем: сначала новые категории, потом по расстоянию/цене
        if max_budget is None:
            # без бюджета — сначала новые категории, потом по цене
            prefer_new_category.sort(
                key=lambda x: (-x[0], -x[2], x[1])
            )
        else:
            # с бюджетом — сначала новые категории, потом по расстоянию
            prefer_new_category.sort(
                key=lambda x: (-x[0], x[1])
            )

        # Берём из топ-3
        candidates_top = prefer_new_category[:min(3, len(prefer_new_category))]
        _, _, _, next_place = random.choice(candidates_top)

        route.append(next_place)
        remaining.remove(next_place)

    # 8. Финальные проверки
    if max_time_hours is not None:
        route = _trim_by_time(route, max_time_hours, min_places)

    if max_budget is not None:
        route = _trim_by_budget(route, max_budget, min_places)

    # 9. При budget=None добираем бесплатными до min_places
    if max_budget is None and len(route) < min_places:
        free_places = [
            p for p in places
            if p not in route and _safe_price(p) == 0
        ]
        for p in free_places:
            if len(route) >= min_places:
                break
            route.append(p)

    return route