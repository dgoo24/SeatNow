"""Persistencia en SQLite (sustituye a PostgreSQL + Prisma en el despliegue).

Streamlit ejecuta cada sesión en su propio hilo, así que se usa una única
conexión con `check_same_thread=False` protegida por un `RLock`. Las
transacciones son reentrantes: `BEGIN IMMEDIATE` solo se abre en el nivel más
externo, lo que permite componer (p. ej. `hold_seat` → `create_booking`)
sin bloquear la base de datos.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seatnow.config import ADMIN_EMAIL, ADMIN_PASSWORD, DEMO_PASSENGERS, DEMO_PASSWORD

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'USER',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    trip_id        TEXT NOT NULL,
    seat_id        TEXT NOT NULL,
    segment_id     TEXT NOT NULL,
    status         TEXT NOT NULL,
    qr_payload     TEXT,
    total_amount   REAL NOT NULL DEFAULT 0,
    failure_reason TEXT,
    expires_at     TEXT,
    is_checked_in  INTEGER NOT NULL DEFAULT 0,
    checked_in_at  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookings_trip   ON bookings(trip_id);
CREATE INDEX IF NOT EXISTS idx_bookings_user   ON bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
"""

_PBKDF2_ROUNDS = 120_000

_lock = threading.RLock()
_connection: sqlite3.Connection | None = None
_depth = 0


# --------------------------------------------------------------------------- #
# Conexión
# --------------------------------------------------------------------------- #

def _candidate_dirs() -> list[Path]:
    override = os.environ.get("SEATNOW_DB_PATH")
    if override:
        return [Path(override).parent]
    return [
        Path(__file__).resolve().parent.parent / "data",
        Path(tempfile.gettempdir()) / "seatnow-data",
    ]


def db_path() -> Path:
    """Ruta del fichero SQLite, con fallback a un directorio temporal."""
    for directory in _candidate_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return directory / "seatnow.db"
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "seatnow.db"


def _connection_() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        conn = sqlite3.connect(
            db_path(), timeout=15, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.executescript(SCHEMA)
        _connection = conn
    return _connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Transacción reentrante con `BEGIN IMMEDIATE` (bloqueo de escritura)."""
    global _depth
    with _lock:
        conn = _connection_()
        outermost = _depth == 0
        if outermost:
            conn.execute("BEGIN IMMEDIATE")
        _depth += 1
        try:
            yield conn
        except BaseException:
            _depth -= 1
            if outermost:
                conn.execute("ROLLBACK")
            raise
        else:
            _depth -= 1
            if outermost:
                conn.execute("COMMIT")


@contextmanager
def reading() -> Iterator[sqlite3.Connection]:
    """Lectura serializada sobre la conexión compartida."""
    with _lock:
        yield _connection_()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# --------------------------------------------------------------------------- #
# Usuarios
# --------------------------------------------------------------------------- #

def init_db() -> None:
    # `executescript` hace un COMMIT implícito: nunca dentro de `transaction()`.
    with reading() as conn:
        conn.executescript(SCHEMA)
    _seed_demo_users()


def _seed_demo_users() -> None:
    if user_count():
        return
    for index in range(1, DEMO_PASSENGERS + 1):
        create_user(
            email=f"pasajero{index}@trenes.local",
            password=DEMO_PASSWORD,
            full_name=f"Pasajero Simulado {index}",
            role="USER",
        )
    create_user(
        email=ADMIN_EMAIL,
        password=ADMIN_PASSWORD,
        full_name="Administración",
        role="ADMIN",
    )


def user_count() -> int:
    with reading() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def create_user(email: str, password: str, full_name: str, role: str = "USER") -> dict[str, Any] | None:
    """Crea un usuario. Devuelve `None` si el email ya existe."""
    email = email.strip().lower()
    with transaction() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, full_name, role, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, email, hash_password(password), full_name, role, now_iso()),
            )
        except sqlite3.IntegrityError:
            return None
    return get_user_by_email(email)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with reading() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, full_name, role, created_at FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    with reading() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, full_name, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    user.pop("password_hash", None)
    return user


# --------------------------------------------------------------------------- #
# Reservas
# --------------------------------------------------------------------------- #

def create_booking(
    *,
    user_id: str,
    trip_id: str,
    seat_id: str,
    segment_id: str,
    status: str,
    total_amount: float,
    expires_at: str | None = None,
    qr_payload: str | None = None,
) -> dict[str, Any]:
    booking_id = uuid.uuid4().hex
    timestamp = now_iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO bookings (id, user_id, trip_id, seat_id, segment_id, status, qr_payload,"
            " total_amount, failure_reason, expires_at, is_checked_in, checked_in_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, NULL, ?, ?)",
            (
                booking_id,
                user_id,
                trip_id,
                seat_id,
                segment_id,
                status,
                qr_payload,
                total_amount,
                expires_at,
                timestamp,
                timestamp,
            ),
        )
    booking = get_booking(booking_id)
    assert booking is not None
    return booking


def get_booking(booking_id: str) -> dict[str, Any] | None:
    with reading() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    return dict(row) if row else None


def list_bookings(*, user_id: str | None = None, trip_id: str | None = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if trip_id is not None:
        clauses.append("trip_id = ?")
        params.append(trip_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with reading() as conn:
        rows = conn.execute(
            f"SELECT * FROM bookings{where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]


def update_booking(booking_id: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_booking(booking_id)
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with transaction() as conn:
        conn.execute(
            f"UPDATE bookings SET {assignments} WHERE id = ?",
            (*fields.values(), booking_id),
        )
    return get_booking(booking_id)


def active_bookings_for_trip(trip_id: str) -> list[dict[str, Any]]:
    """Reservas vivas (hold o confirmadas) de un viaje, para el inventario."""
    with reading() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE trip_id = ? AND status IN ('PENDING_PAYMENT', 'CONFIRMED')",
            (trip_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def active_bookings() -> list[dict[str, Any]]:
    """Todas las reservas vivas, agrupables por `trip_id` en memoria."""
    with reading() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE status IN ('PENDING_PAYMENT', 'CONFIRMED')"
        ).fetchall()
    return [dict(row) for row in rows]
