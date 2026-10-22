"""Regresión de la cabecera: navegación propia, sin barra lateral y con nº de tren."""

from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "streamlit_app.py"


def _run() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=90).run()
    assert not at.exception
    return at


def _html(at: AppTest) -> str:
    return "".join(element.value for element in at.get("html"))


def test_header_has_navigation_and_no_sidebar() -> None:
    at = _run()
    links = [link.value for link in at.get("page_link")]
    assert links == [
        "Verbindung suchen",
        "Wagen & Sitzplatz",
        "Meine Reservierungen",
        "Administration",
    ]
    assert len(at.sidebar) == 0


def test_header_shows_brand_and_meta_strip() -> None:
    html = _html(_run())
    assert "SeatNow" in html
    assert "Züge MVP" not in html
    assert 'class="sn-meta"' in html
    assert "Bestand" in html
    assert "Datenquelle" in html
    assert "pasajero1@trenes.local" in html


def test_search_results_show_train_number() -> None:
    at = _run()
    next(button for button in at.button if button.label == "Zug finden").click()
    at.run()
    assert not at.exception
    numbers = re.findall(r'<span class="sn-train-no">([^<]+)</span>', _html(at))
    assert numbers
    assert all(number.isdigit() for number in numbers)


def test_trip_header_uses_train_number() -> None:
    at = _run()
    at.switch_page("app_pages/wagen.py")
    at.run()
    assert not at.exception
    title = re.search(r'class="sn-trip-title">([^<]+)<', _html(at))
    assert title is not None
    assert re.search(r"\w+ \d+", title.group(1))
