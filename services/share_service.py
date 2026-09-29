'''
Скрипт для реализации возможности поделиться сгенерированным маршуртом с помощью pdf-файла
'''

import os
import uuid
import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
)
# пути

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
PDF_DIR = STORAGE_DIR / "pdf"

PDF_DIR.mkdir(parents=True, exist_ok=True)


_FONT_NAME = "Helvetica"  # fallback
_FONT_REGISTERED = False


def _register_font():
    global _FONT_NAME, _FONT_REGISTERED
    if _FONT_REGISTERED:
        return

    candidates = [
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/DejaVuSans.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("MainFont", path))
                _FONT_NAME = "MainFont"
                _FONT_REGISTERED = True
                return
            except Exception:
                continue

    _FONT_REGISTERED = True



# генерирует PDF
def generate_route_pdf(route_payload: dict) -> Path:
    """
    Генерирует PDF с маршрутом и возвращает путь к файлу.
    Без emoji — reportlab их не поддерживает со стандартными шрифтами.
    """
    _register_font()

    file_id = uuid.uuid4().hex[:12]
    path = PDF_DIR / f"route_{file_id}.pdf"

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="Маршрут по городу",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleCyr",
        parent=styles["Title"],
        fontName=_FONT_NAME,
        fontSize=20,
        spaceAfter=6,
        alignment=1,
    )
    subtitle_style = ParagraphStyle(
        "SubCyr",
        parent=styles["BodyText"],
        fontName=_FONT_NAME,
        fontSize=13,
        textColor="#555555",
        alignment=1,
        spaceAfter=14,
    )
    meta_style = ParagraphStyle(
        "MetaCyr",
        parent=styles["BodyText"],
        fontName=_FONT_NAME,
        fontSize=11,
        textColor="#333333",
        leading=15,
        spaceAfter=4,
    )
    h2_style = ParagraphStyle(
        "H2Cyr",
        parent=styles["Heading2"],
        fontName=_FONT_NAME,
        fontSize=14,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyCyr",
        parent=styles["BodyText"],
        fontName=_FONT_NAME,
        fontSize=11,
        leading=15,
    )
    small_style = ParagraphStyle(
        "SmallCyr",
        parent=styles["BodyText"],
        fontName=_FONT_NAME,
        fontSize=10,
        textColor="#777777",
        leading=13,
    )

    story = []

    # --- Заголовок ---
    story.append(Paragraph("Маршрут по городу", title_style))
    story.append(
        Paragraph(
            route_payload.get("city_name", "—"),
            subtitle_style,
        )
    )


    # --- Мета ---
    hours = route_payload.get("hours", "—")
    budget_text = route_payload.get("budget_text", "—")

    story.append(
        Paragraph(
            f"<b>Время:</b> около {hours} ч. &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Бюджет:</b> {budget_text}",
            meta_style,
        )
    )

    category_names = route_payload.get("category_names") or []
    if category_names:
        story.append(
            Paragraph(
                f"<b>Интересы:</b> {', '.join(category_names)}",
                meta_style,
            )
        )

    story.append(Spacer(1, 0.5 * cm))

    # КАРТА МАРШРУТА

    map_path = route_payload.get("map_path")
    if map_path and os.path.exists(map_path):
        try:
            # Карта: ширина под страницу, высота автоматически
            img = Image(map_path)
            img_width = 17 * cm   # A4 минус отступы
            img_height = img.imageHeight * (img_width / img.imageWidth)
            img.drawWidth = img_width
            img.drawHeight = img_height

            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph("Карта маршрута:", body_style))
            story.append(Spacer(1, 0.2 * cm))
            story.append(img)
            story.append(Spacer(1, 0.5 * cm))
        except Exception:
            logging.exception("Не удалось вставить карту в PDF")

    # ССЫЛКА НА ЯНДЕКС.КАРТЫ

    map_link = route_payload.get("map_link")
    if map_link:
        story.append(Spacer(1, 0.3 * cm))
        # Кликабельная ссылка через <link>
        link_html = (
            f'<link href="{map_link}">'
            f'🗺 Открыть маршрут на Яндекс.Картах'
            f'</link>'
        )
        story.append(Paragraph(link_html, body_style))
        story.append(Spacer(1, 0.5 * cm))

    # Места
    places = route_payload.get("places", [])
    for index, place in enumerate(places, start=1):
        story.append(
            Paragraph(
                f"{index}. {place.get('name', 'Без названия')}",
                h2_style,
            )
        )

        address = place.get("address") or "Адрес не указан"
        story.append(
            Paragraph(f"<b>Адрес:</b> {address}", body_style)
        )

        price = place.get("price", 0)
        price_text = "бесплатно" if price == 0 else f"{price} руб."
        story.append(
            Paragraph(f"<b>Стоимость:</b> {price_text}", body_style)
        )

        description = place.get("description")
        if description:
            story.append(
                Paragraph(
                    f"<b>Описание:</b> {description}",
                    body_style,
                )
            )

        ticket_url = place.get("ticket_url")
        if ticket_url:
            story.append(
                Paragraph(
                    f"<b>Билеты:</b> {ticket_url}",
                    small_style,
                )
            )

        source_url = place.get("source_url")
        if source_url:
            story.append(
                Paragraph(
                    f"<b>Узнать больше:</b> {source_url}",
                    small_style,
                )
            )

        story.append(Spacer(1, 0.4 * cm))

    # итог работы
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        Paragraph(
            f"<b>Всего мест:</b> {len(places)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Расстояние:</b> "
            f"{route_payload.get('total_distance', 0):.1f} км "
            f"&nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Стоимость:</b> "
            f"{route_payload.get('total_price', 0)} руб.",
            body_style,
        )
    )

    story.append(Spacer(1, 0.8 * cm))
    story.append(
        Paragraph(
            f"Сгенерировано: "
            f"{datetime.now().strftime('%d.%m.%Y %H:%M')}",
            small_style,
        )
    )

    doc.build(story)
    return path