"""Configuración de la app SeatNow."""

from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

GTFS_DIR = Path(os.environ.get("SEATNOW_GTFS_DIR", APP_ROOT / "backend" / "gtfs-data"))

CAR_COUNT = 2
SEATS_PER_CAR = 25
SEATS_PER_TRIP = CAR_COUNT * SEATS_PER_CAR

SEAT_HOLD_TTL_SECONDS = int(os.environ.get("SEATNOW_HOLD_TTL_SECONDS", "180"))
NO_SHOW_GRACE_MINUTES = int(os.environ.get("SEATNOW_NO_SHOW_GRACE_MINUTES", "15"))
NO_SHOW_MAX_AGE_HOURS = 6
CRITICAL_DELAY_MINUTES = 60

PAYMENTS_MODE = "mock"
MAIL_MODE = "disabled"

TIMEZONE = os.environ.get("SEATNOW_TIMEZONE", "Europe/Madrid")

DEMO_PASSENGERS = 10
DEMO_PASSWORD = "demo1234"
ADMIN_EMAIL = "admin@trenes.local"
ADMIN_PASSWORD = "admin1234"

ACTIVE_BOOKING_STATUSES = ("PENDING_PAYMENT", "CONFIRMED")
