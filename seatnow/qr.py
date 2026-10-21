"""Generación del código QR de los billetes."""

from __future__ import annotations

import io


def qr_png(payload: str) -> bytes | None:
    """Devuelve el PNG del QR o None si la librería no está disponible."""
    if not payload:
        return None
    try:
        import qrcode
    except ImportError:  # pragma: no cover
        return None
    try:
        image = qrcode.make(payload).get_image()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except (OSError, ValueError):  # pragma: no cover
        return None
