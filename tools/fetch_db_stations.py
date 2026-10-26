"""Descarga el catálogo de estaciones de la DB API (StaDa v2) a data/db_stations.json.

Uso:
    DB_CLIENT_ID=... DB_API_KEY=... python tools/fetch_db_stations.py

El resultado es un JSON ligero ({name, eva, category, lat, lon, city}) que usa
``db_backend.py`` para el autocompletado sin llamar a la API en cada búsqueda.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

BASE = "https://apis.deutschebahn.com/db-api-marketplace/apis/station-data/v2/stations"
TARGET = Path(__file__).resolve().parent.parent / "data" / "db_stations.json"


def main() -> None:
    client_id = os.environ.get("DB_CLIENT_ID")
    api_key = os.environ.get("DB_API_KEY")
    if not client_id or not api_key:
        raise SystemExit("Faltan DB_CLIENT_ID / DB_API_KEY en el entorno.")

    headers = {"DB-Client-Id": client_id, "DB-Api-Key": api_key, "Accept": "application/json"}
    rows: list[dict[str, object]] = []
    offset = 0
    while True:
        response = requests.get(
            BASE,
            params={"offset": offset, "limit": 5000},
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("result") or []
        if not batch:
            break
        for station in batch:
            evas = station.get("evaNumbers") or []
            main = next((item for item in evas if item.get("isMain")), evas[0] if evas else {})
            eva = main.get("number")
            coords = (main.get("geographicCoordinates") or {}).get("coordinates") or []
            if not eva or len(coords) != 2:
                continue
            rows.append(
                {
                    "name": station.get("name"),
                    "eva": str(eva),
                    "evas": [str(item["number"]) for item in evas if item.get("number")],
                    "category": station.get("category"),
                    "lat": coords[1],
                    "lon": coords[0],
                    "city": (station.get("mailingAddress") or {}).get("city"),
                }
            )
        offset += len(batch)
        if offset >= int(payload.get("total") or 0):
            break

    rows.sort(key=lambda item: (str(item["name"]).lower(), str(item["eva"])))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rows)} estaciones escritas en {TARGET} ({TARGET.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
