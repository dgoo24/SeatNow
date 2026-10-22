"""Tests del cliente de la Deutsche Bahn Timetables API (sin red)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from seatnow import dbapi, dbfeed
from seatnow.dbapi import StopEvent

PLAN_XML = """<?xml version="1.0" encoding="utf-8"?>
<timetable station="8011160" eva="8011160" version="1.9">
  <s id="1|30080|9|80|260822|22" eva="8011160" dir="-">
    <tl c="ICE" n="799"/>
    <ar pt="2608222212" ct="2608222215" pp="3" cp="4" ps="p" cs="p" l="ICE 799"
       ppth="München Hbf|Hannover Hbf|Berlin Hbf"/>
    <dp pt="2608222218" ct="2608222221" pp="4" cp="5" ps="p" cs="p" l="ICE 799"
       ppth="München Hbf|Hannover Hbf|Berlin Hbf"/>
  </s>
  <s id="1|30080|9|80|260822|23" eva="8011160" dir="-">
    <tl c="RE" n="1"/>
    <dp pt="2608222230" pp="7" ps="p" l="RE 1"
       ppth="Berlin Hbf|Magdeburg Hbf|Hannover Hbf"/>
  </s>
</timetable>
"""

CHANGES_XML = """<?xml version="1.0" encoding="utf-8"?>
<timetable station="8011160" eva="8011160" version="1.9">
  <s id="1|30080|9|80|260822|22" eva="8011160" dir="-">
    <tl c="ICE" n="799"/>
    <dp ct="2608222224" cp="6" cs="p" l="ICE 799"
       ppth="München Hbf|Hannover Hbf|Berlin Hbf"/>
  </s>
</timetable>
"""

STATION_XML = """<?xml version="1.0" encoding="utf-8"?>
<timetable version="1.9">
  <s name="Berlin Hbf" eva="8011160" ds100="BER" />
  <s name="Berlin Südkreuz" eva="8011165" ds100="BWS" />
  <s name="Berlin Ostbahnhof" eva="8011167" ds100="BOF" />
</timetable>
"""

LEGACY_STATION_XML = """<?xml version="1.0" encoding="utf-8"?>
<stations>
  <station name="Berlin Hbf" eva="8011160" ds100="BER" />
</stations>
"""


def test_parse_timetable_reads_planned_times() -> None:
    events = dbapi.parse_timetable(PLAN_XML)
    assert len(events) == 2

    ice = events[0]
    assert ice.category == "ICE"
    assert ice.number == "799"
    assert ice.train == "ICE 799"
    assert ice.arrival_planned == datetime(2026, 8, 22, 22, 12, tzinfo=dbapi.LOCAL_TZ)
    assert ice.departure_planned == datetime(2026, 8, 22, 22, 18, tzinfo=dbapi.LOCAL_TZ)
    assert ice.platform == "5"  # la vía de salida manda sobre la de llegada
    assert ice.destination == "Berlin Hbf"
    assert ice.origin == "München Hbf"
    assert not ice.cancelled


def test_parse_timetable_reads_actual_times() -> None:
    ice = dbapi.parse_timetable(PLAN_XML)[0]
    assert ice.arrival_actual == datetime(2026, 8, 22, 22, 15, tzinfo=dbapi.LOCAL_TZ)
    assert ice.departure_actual == datetime(2026, 8, 22, 22, 21, tzinfo=dbapi.LOCAL_TZ)
    assert ice.delay_minutes == 3


def test_parse_stations() -> None:
    stations = dbapi.parse_stations(STATION_XML)
    assert [s.eva for s in stations] == ["8011160", "8011165", "8011167"]
    assert stations[0].name == "Berlin Hbf"
    assert stations[0].ds100 == "BER"


def test_parse_stations_accepts_legacy_format() -> None:
    stations = dbapi.parse_stations(LEGACY_STATION_XML)
    assert [(s.eva, s.name) for s in stations] == [("8011160", "Berlin Hbf")]


def test_merge_changes_applies_delay_and_platform() -> None:
    planned = dbapi.parse_timetable(PLAN_XML)[0]
    change = dbapi.parse_timetable(CHANGES_XML)[0]
    merged = dbapi.merge_changes(planned, change)
    assert merged.departure_actual == datetime(2026, 8, 22, 22, 24, tzinfo=dbapi.LOCAL_TZ)
    assert merged.platform == "6"
    assert merged.delay_minutes == 6


def test_merge_changes_keeps_plan_without_change() -> None:
    planned = dbapi.parse_timetable(PLAN_XML)[1]
    merged = dbapi.merge_changes(planned, None)
    assert merged == planned


def test_matches_compares_category_number_and_origin() -> None:
    ice = dbapi.parse_timetable(PLAN_XML)[0]
    assert dbapi.matches(ice, "ICE", "799", ice.route_origin)
    assert not dbapi.matches(ice, "RE", "799", ice.route_origin)
    assert not dbapi.matches(ice, "ICE", "123", ice.route_origin)
    assert not dbapi.matches(ice, "ICE", "799", "Hamburg Hbf")


def test_parse_ris_time_ignores_invalid_values() -> None:
    assert dbapi._parse_ris_time(None) is None
    assert dbapi._parse_ris_time("") is None
    assert dbapi._parse_ris_time("2608222212") == datetime(
        2026, 8, 22, 22, 12, tzinfo=dbapi.LOCAL_TZ
    )


def test_stop_event_destination_and_origin() -> None:
    event = StopEvent(
        id="x",
        eva="8011160",
        category="ICE",
        number="799",
        path=("Berlin Hbf", "Hannover Hbf", "Hamburg Hbf"),
    )
    assert event.origin == "Berlin Hbf"
    assert event.destination == "Hamburg Hbf"


def test_search_hours_window() -> None:
    now = datetime(2026, 8, 22, 9, 30, tzinfo=dbapi.LOCAL_TZ)
    assert list(dbfeed.search_hours(date(2026, 8, 22), 600, 720, now=now)) == [10, 11, 12]
    assert list(dbfeed.search_hours(date(2026, 8, 22), None, None, now=now)) == [9, 10, 11, 12]
    # Otros días: se empieza por la primera hora del día.
    assert list(dbfeed.search_hours(date(2026, 8, 23), None, None, now=now)) == [0, 1, 2, 3]


def test_search_hours_respects_max_hours() -> None:
    now = datetime(2026, 8, 22, 9, 30, tzinfo=dbapi.LOCAL_TZ)
    hours = list(dbfeed.search_hours(date(2026, 8, 22), 600, 1200, now=now, max_hours=3))
    assert hours == [10, 11, 12]


def test_name_variants_include_ascii_and_wildcard() -> None:
    variants = dbfeed._name_variants("Köln Hbf")
    assert "Köln Hbf" in variants
    assert "Koeln Hbf" in variants
    assert "Köln Hbf*" in variants
    assert "Koeln Hbf*" in variants


def test_call_budget_limits_requests() -> None:
    budget = dbfeed.CallBudget(3)
    assert budget.take()
    assert budget.take()
    assert budget.take()
    assert not budget.take()
    assert budget.remaining == 0


def test_index_of_matches_name() -> None:
    names = ["Berlin Hbf", "Hannover Hbf", "Hamburg Hbf"]
    assert dbfeed._index_of(names, "Hannover Hbf") == 1
    assert dbfeed._index_of(names, "Hamburg") == 2
    assert dbfeed._index_of(names, "Utrecht") == 0


def test_parse_error_is_reported_as_db_error() -> None:
    with pytest.raises(dbapi.DbApiError):
        dbapi.parse_timetable(b"<not-xml")
    with pytest.raises(dbapi.DbApiError):
        dbapi.parse_stations(b"<not-xml")


def test_plan_retries_with_full_year(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _get(path: str, *, client_id: str, api_key: str, timeout: float = 10) -> bytes:
        calls.append(path)
        if path.startswith("plan/8011160/26"):
            raise dbapi.DbApiError("nope")
        return PLAN_XML.encode()

    monkeypatch.setattr(dbapi, "_get", _get)
    events = dbapi.plan("8011160", date(2026, 8, 22), 22, client_id="c", api_key="k")
    assert events
    assert calls == ["plan/8011160/260822/22", "plan/8011160/20260822/22"]
