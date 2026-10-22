"""Prueba de extremo a extremo del feed real (con la API simulada)."""

from __future__ import annotations

from datetime import date

import pytest

from seatnow import dbapi, dbfeed

STATIONS_XML = """<?xml version="1.0" encoding="utf-8"?>
<stations>
  <station name="Berlin Hbf" eva="8011160" ds100="BER" />
  <station name="Berlin Südkreuz" eva="8011165" ds100="BWS" />
  <station name="Hannover Hbf" eva="8000240" ds100="HBF" />
  <station name="München Hbf" eva="8000261" ds100="MUC" />
  <station name="Magdeburg Hbf" eva="8011169" ds100="MD" />
</stations>
"""


def _plan_xml(hour: int) -> str:
    """RIS real: en la llegada la ruta llega hasta la estación, en la salida
    la ruta empieza en la estación. El ICE 799 termina en Berlin Hbf."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<timetable station="8011160" eva="8011160" version="1.9">
  <s id="ice-{hour}" eva="8011160" dir="-">
    <tl c="ICE" n="799"/>
    <ar pt="260822{hour:02d}12" ct="260822{hour:02d}15" pp="3" cp="4" ps="p" cs="p" l="ICE 799"
       ppth="München Hbf|Hannover Hbf|Berlin Hbf"/>
    <dp pt="260822{hour:02d}18" ct="260822{hour:02d}21" pp="4" cp="5" ps="p" cs="p" l="ICE 799"
       ppth="Berlin Hbf"/>
  </s>
  <s id="re-{hour}" eva="8011160" dir="-">
    <tl c="RE" n="1"/>
    <dp pt="260822{hour:02d}30" pp="7" ps="p" l="RE 1"
       ppth="Berlin Hbf|Magdeburg Hbf|Hannover Hbf"/>
  </s>
</timetable>
"""


CHANGES_XML = """<?xml version="1.0" encoding="utf-8"?>
<timetable station="8011160" eva="8011160" version="1.9">
  <s id="ice-0" eva="8011160" dir="-">
    <tl c="ICE" n="799"/>
    <dp ct="2608220024" cp="6" cs="p" l="ICE 799"
       ppth="München Hbf|Hannover Hbf|Berlin Hbf"/>
  </s>
</timetable>
"""

HANNOVER_PLAN_XML = """<?xml version="1.0" encoding="utf-8"?>
<timetable station="8000240" eva="8000240" version="1.9">
  <s id="re-0" eva="8000240" dir="-">
    <tl c="RE" n="1"/>
    <ar pt="2608220055" pp="7" ps="p" l="RE 1"
       ppth="Berlin Hbf|Magdeburg Hbf|Hannover Hbf"/>
  </s>
</timetable>
"""

EMPTY_TIMETABLE = """<?xml version="1.0" encoding="utf-8"?>
<timetable version="1.9"></timetable>
"""


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    dbfeed.cached_plan.clear()
    dbfeed.cached_changes.clear()
    dbfeed.cached_stations.clear()


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get(path: str, *, client_id: str, api_key: str, timeout: float = 10) -> bytes:
        if path.startswith("station/"):
            return STATIONS_XML.encode()
        if path.startswith("plan/"):
            hour = int(path.rsplit("/", 1)[-1])
            return _plan_xml(hour).encode()
        if path.startswith("fchg/"):
            return CHANGES_XML.encode()
        raise AssertionError(f"ruta inesperada: {path}")

    monkeypatch.setattr(dbapi, "_get", _get)
    monkeypatch.setattr(dbapi, "credentials", lambda: ("client", "key"))


def test_resolve_station_returns_eva(fake_api: None) -> None:
    station = dbfeed.resolve_station("Berlin Hbf")
    assert station is not None
    assert station.eva == "8011160"
    assert station.name == "Berlin Hbf"


def test_search_builds_feed_and_results(fake_api: None) -> None:
    origin = dbfeed.resolve_station("Berlin Hbf")
    assert origin is not None
    feed, results = dbfeed.search(origin, None, date(2026, 8, 22), None, None)

    assert feed.trips
    assert feed.segments
    assert results

    first = results[0]
    assert first["origin_stop"] == "Berlin Hbf"
    assert first["train"] == "RE"
    assert first["departure"] == "00:30:00"
    assert first["total_seats"] == 50
    assert first["available_seats"] == 50
    assert first["duration_minutes"] > 0
    assert first["price"] > 0

    trip = feed.trips[first["trip_id"]]
    assert trip.stops
    assert trip.live is not None
    assert trip.live["stops"]


def test_terminating_train_is_not_a_departure(fake_api: None) -> None:
    """El ICE 799 termina en Berlin Hbf: no es una salida desde allí."""
    origin = dbfeed.resolve_station("Berlin Hbf")
    assert origin is not None
    _, results = dbfeed.search(origin, None, date(2026, 8, 22), None, None)
    assert all(result["train"] != "ICE" for result in results)


def test_search_filters_by_destination(fake_api: None) -> None:
    origin = dbfeed.resolve_station("Berlin Hbf")
    destination = dbfeed.resolve_station("Hannover Hbf")
    assert origin is not None and destination is not None
    _, results = dbfeed.search(origin, destination, date(2026, 8, 22), None, None)
    assert results
    assert all(result["destination_stop"] == "Hannover Hbf" for result in results)


def test_trip_uses_real_arrival_and_keeps_times_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La hora de llegada viene de la API y las paradas van en orden."""

    def _get(path: str, *, client_id: str, api_key: str, timeout: float = 10) -> bytes:
        if path.startswith("station/"):
            return STATIONS_XML.encode()
        if path.startswith("plan/"):
            parts = path.split("/")
            eva, hour = parts[1], int(parts[-1])
            if eva == "8000240":
                return (HANNOVER_PLAN_XML if hour == 0 else EMPTY_TIMETABLE).encode()
            return _plan_xml(hour).encode()
        if path.startswith("fchg/"):
            return CHANGES_XML.encode()
        raise AssertionError(f"ruta inesperada: {path}")

    monkeypatch.setattr(dbapi, "_get", _get)
    monkeypatch.setattr(dbapi, "credentials", lambda: ("client", "key"))
    dbfeed.cached_plan.clear()
    dbfeed.cached_changes.clear()
    dbfeed.cached_stations.clear()

    origin = dbfeed.resolve_station("Berlin Hbf")
    destination = dbfeed.resolve_station("Hannover Hbf")
    assert origin is not None and destination is not None
    feed, results = dbfeed.search(origin, destination, date(2026, 8, 22), None, None)
    assert results

    trip = feed.trips[results[0]["trip_id"]]
    arrivals = [stop.arrival for stop in trip.stops]
    assert arrivals == sorted(arrivals)
    assert trip.stops[-1].arrival == "00:55:00"
    assert results[0]["arrival"] == "00:55:00"
