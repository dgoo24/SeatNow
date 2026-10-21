"""SeatNow · lógica de dominio compartida.

Port a Python de la lógica del backend Fastify (`backend/src`) para poder
desplegar la aplicación como app de Streamlit sin PostgreSQL ni Redis.
"""

__all__ = ["config", "format", "gtfs", "inventory", "qr", "realtime", "search", "store"]
