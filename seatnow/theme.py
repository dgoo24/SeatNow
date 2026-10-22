"""Tema visual de la app: puerto del diseño de `backend/public/index.html`.

El dashboard original es una SPA con una paleta ferroviaria (rojo DB sobre
fondo nocturno), tarjetas con borde superior rojo, mapa de asientos en forma
de butaca y panel de live-status ámbar. Aquí se traduce ese CSS a los
selectores de Streamlit y se exponen builders de HTML para las partes que no
existen como widget nativo (cabecera de marca, chips, leyenda, panel QR).
"""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from seatnow.config import CAR_COUNT, MAIL_MODE, PAYMENTS_MODE, SEATS_PER_CAR
from seatnow.format import local_now

# `st.html` sanea el contenido con DOMPurify, que elimina el SVG inline.
# Por eso el logo se inyecta como data-URI dentro del CSS (el CSS no se sanea).
BRAND_SVG_URI = (
    "data:image/svg+xml;charset=utf-8,"
    "%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E"
    "%3Cpath%20d='M8%207h16v13a4%204%200%200%201-4%204h-1.8L16%2028l-2.2-4H12a4%204%200%200%201-4-4V7z'%20fill='%23fff'/%3E"
    "%3Crect%20x='11'%20y='11'%20width='4.4'%20height='5'%20rx='1.2'%20fill='%23ec0016'/%3E"
    "%3Crect%20x='16.6'%20y='11'%20width='4.4'%20height='5'%20rx='1.2'%20fill='%23ec0016'/%3E"
    "%3Crect%20x='10'%20y='26.5'%20width='3'%20height='3.5'%20rx='1.5'%20fill='%23fff'/%3E"
    "%3Crect%20x='19'%20y='26.5'%20width='3'%20height='3.5'%20rx='1.5'%20fill='%23fff'/%3E"
    "%3C/svg%3E"
)

CSS = """
/* ===== tokens: paleta ferroviaria (DB) sobre fondo nocturno ===== */
:root {
  --db-red: #ec0016;
  --db-red-dark: #b60011;
  --bg: #0b0d14;
  --bg2: #0f1220;
  --panel: #151a2a;
  --panel2: #101527;
  --border: #262e47;
  --border-soft: #1d2440;
  --text: #eef1f8;
  --muted: #98a2b8;
  --blue: #3b82f6;
  --green: #21c17a;
  --amber: #f5a300;
  --red: #ef4444;
  --mono: 'Roboto Mono', ui-monospace, SFMono-Regular, Consolas, monospace;
  --sans: 'Roboto', 'Segoe UI', system-ui, sans-serif;
  --cond: 'Roboto Condensed', 'Arial Narrow', system-ui, sans-serif;
}

/* ===== fondo de la app ===== */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stMainBlockContainer"], [data-testid="stSidebar"] {
  background:
    radial-gradient(1100px 500px at 85% -100px, rgba(236, 0, 22, .16), transparent 60%),
    radial-gradient(900px 420px at -10% 20%, rgba(59, 130, 246, .10), transparent 55%),
    repeating-linear-gradient(-45deg, transparent 0 26px, rgba(255,255,255,.014) 26px 27px),
    var(--bg) !important;
  color: var(--text);
  font-family: var(--sans);
}
[data-testid="stSidebar"] {
  background:
    radial-gradient(700px 320px at 110% -60px, rgba(236, 0, 22, .12), transparent 60%),
    var(--bg2) !important;
  border-right: 1px solid var(--border-soft);
}
/* La barra superior nativa estorba: la app usa su propia cabecera. */
[data-testid="stHeader"] { display: none !important; }
[data-testid="stMain"] { padding-top: 0; }
/* Contenido centrado a 1080px como el `main` del HTML. */
[data-testid="stMainBlockContainer"] {
  max-width: 1080px !important;
  margin: 0 auto !important;
  padding: 0 20px 60px !important;
}
h1, h2, h3, h4, h5, h6 { font-family: var(--cond) !important; letter-spacing: .02em; }
a { color: #9fc3ff; }

/* ===== cabecera de la app (estilo DB) ===== */
div[class*="st-key-sn_appbar"] {
  position: sticky; top: 0; z-index: 40;
  width: calc(100% + 40px);
  margin-left: -20px;
  margin-right: -20px;
  margin-bottom: 18px;
  padding: 10px 26px 10px;
  background:
    radial-gradient(900px 220px at 90% -140px, rgba(236, 0, 22, .14), transparent 60%),
    linear-gradient(180deg, rgba(12, 15, 26, .98), rgba(9, 11, 18, .96));
  border: none;
  border-bottom: 2px solid var(--db-red);
  border-radius: 0;
  box-shadow: 0 12px 36px rgba(0, 0, 0, .5);
  backdrop-filter: blur(10px);
}
div[class*="st-key-sn_appbar"]::before {
  content: ''; position: absolute; left: 0; right: 0; bottom: -2px; height: 2px;
  background: linear-gradient(90deg, var(--db-red), #ff6b7a 40%, transparent 85%);
}
div[class*="st-key-sn_appbar"] [data-testid="stHorizontalBlock"] {
  align-items: center;
}
div[class*="st-key-sn_appbar"] [data-testid="column"] {
  display: flex;
  align-items: center;
}
div[class*="st-key-sn_appbar"] [data-testid="column"]:nth-child(3) {
  justify-content: flex-end;
}
.sn-brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
.sn-mark {
  width: 46px; height: 46px; border-radius: 12px; flex: 0 0 auto;
  background: linear-gradient(145deg, var(--db-red), var(--db-red-dark));
  display: grid; place-items: center; position: relative;
  box-shadow: 0 6px 18px rgba(236,0,22,.45), inset 0 1px 0 rgba(255,255,255,.35);
}
.sn-mark::before {
  content: ''; position: absolute; inset: 9px 9px 11px 9px;
  background-image: url("__BRAND_SVG_URI__");
  background-repeat: no-repeat; background-position: center; background-size: contain;
}
.sn-title {
  font-family: var(--cond); font-size: 26px; font-weight: 700; margin: 0;
  letter-spacing: .01em; text-transform: uppercase; line-height: 1.05;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sn-title small {
  display: block; font-family: var(--sans); font-size: 11.5px; font-weight: 400;
  text-transform: none; letter-spacing: .04em; color: var(--muted); margin-top: 3px; white-space: normal;
}
.sn-badges { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; justify-content: flex-end; }
.sn-badge {
  font-size: 11px; font-weight: 500; letter-spacing: .03em;
  padding: 5px 11px; border-radius: 999px;
  background: var(--panel2); border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
}
.sn-badge.ok { color: #7ef0bb; border-color: rgba(33,193,122,.4); background: rgba(33,193,122,.10); }
.sn-badge.warn { color: #ffd67a; border-color: rgba(245,163,0,.4); background: rgba(245,163,0,.10); }

/* ===== navegación dentro del dashboard (barra tipo DB) ===== */
div[class*="st-key-sn_pagebar"] {
  margin: 0 0 16px;
  padding: 4px 6px;
  background: radial-gradient(600px 80px at 50% -40px, rgba(236, 0, 22, .10), transparent 70%),
              linear-gradient(180deg, rgba(22, 26, 44, .9), rgba(14, 17, 30, .9));
  border: 1px solid var(--border);
  border-radius: 12px;
  width: 100%;
  box-shadow: 0 10px 28px rgba(0, 0, 0, .32);
  justify-content: center;
}
div[class*="st-key-sn_pagebar"] [data-testid="stHorizontalBlock"] {
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
}
div[class*="st-key-sn_nav_"] .stPageLink a { width: auto !important; }
div[class*="st-key-sn_nav_"] a[data-testid="stPageLink-NavLink"] {
  text-decoration: none !important;
  font-family: var(--cond) !important; font-size: 13.5px; font-weight: 700 !important;
  text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted) !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 999px !important;
  padding: 8px 18px !important; margin: 0 !important;
  display: inline-flex !important; align-items: center !important; gap: 8px !important;
  white-space: nowrap; box-shadow: none !important;
  transition: color .15s, background .15s, border-color .15s, transform .12s;
}
div[class*="st-key-sn_nav_"] a[data-testid="stPageLink-NavLink"] span { color: inherit !important; }
div[class*="st-key-sn_nav_"] a[data-testid="stPageLink-NavLink"] span[data-testid="stIconMaterial"] {
  color: inherit !important;
}
div[class*="st-key-sn_nav_"] a[data-testid="stPageLink-NavLink"]:hover {
  color: var(--text) !important; background: rgba(255, 255, 255, .06) !important;
  border-color: rgba(255, 255, 255, .12) !important; transform: translateY(-1px);
}
div[class*="st-key-sn_nav_"][class*="_active"] a[data-testid="stPageLink-NavLink"] {
  color: #fff !important;
  background: linear-gradient(160deg, var(--db-red), var(--db-red-dark)) !important;
  border-color: rgba(255, 255, 255, .22) !important;
  box-shadow: 0 8px 18px rgba(236, 0, 22, .38) !important;
}

/* ===== franja de metadatos (bestand, fuente, pago, hora) ===== */
.sn-meta {
  display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: center;
  margin-top: 12px; padding-top: 11px; border-top: 1px solid var(--border-soft);
  font-size: 12.5px; color: var(--muted);
}
.sn-meta-item { display: inline-flex; align-items: baseline; gap: 7px; }
.sn-meta-item b {
  font-family: var(--cond); font-size: 10.5px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase; color: #ffb3ba;
}
.sn-meta-item span { color: var(--text); }

/* ===== cuenta (cabecera) ===== */
.sn-account { text-align: right; line-height: 1.25; }
.sn-account b { display: block; font-size: 13px; color: var(--text); }
.sn-account span { font-size: 11.5px; color: var(--muted); }

/* ===== tarjetas ===== */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background:
    radial-gradient(700px 200px at 90% -80px, rgba(236, 0, 22, .10), transparent 60%),
    linear-gradient(180deg, rgba(26, 30, 52, .98), rgba(16, 20, 36, .98));
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 22px 50px rgba(0,0,0,.44);
  position: relative;
  overflow: hidden;
}
div[data-testid="stVerticalBlockBorderWrapper"]::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--db-red), transparent 65%);
}
.sn-card-title {
  font-family: var(--cond); text-transform: uppercase; letter-spacing: .08em;
  font-size: 13px; font-weight: 700; color: #ffb3ba; margin: 0 0 14px;
  display: flex; align-items: center; gap: 8px;
}
.sn-card-title::before {
  content: ''; width: 10px; height: 10px; border-radius: 3px;
  background: var(--db-red); box-shadow: 0 0 10px rgba(236,0,22,.7); transform: rotate(45deg);
}
.sn-card-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  flex-wrap: wrap; margin-bottom: 14px;
}
.sn-card-head .sn-card-title { margin: 0; }

/* ===== etiquetas y widgets ===== */
div[data-testid="stWidgetLabel"] p {
  font-size: 10.5px !important; font-weight: 700 !important; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted) !important; margin-bottom: 6px !important;
}
div[data-baseweb="select"] > div,
div[data-baseweb="input"] > div,
div[data-baseweb="base-input"] > div {
  background: #0d1120 !important;
  border: 1px solid var(--border) !important;
  border-radius: 11px !important;
}
div[data-baseweb="select"] > div:focus-within {
  border-color: var(--db-red) !important;
  box-shadow: 0 0 0 3px rgba(236,0,22,.22) !important;
}
input, textarea, [data-baseweb="select"] input {
  font-family: var(--sans) !important;
  color: var(--text) !important;
}
[data-testid="stDateInput"] input, [data-testid="stTimeInput"] input { color: var(--text) !important; }
[data-testid="stToggleWidget"] label p { color: var(--text) !important; }
[data-testid="stWidgetLabel"] + div p { color: var(--text); }

/* ===== botones ===== */
div[data-testid="stButton"] button,
div[data-testid="stFormSubmitButton"] button {
  font-family: var(--sans); font-size: 14px; font-weight: 700; letter-spacing: .02em;
  border-radius: 11px !important; padding: 10px 18px !important;
  border: 1px solid var(--border) !important;
  background: #232b45 !important; color: var(--text) !important;
  box-shadow: none !important;
  transition: transform .12s, box-shadow .15s, filter .15s, background .15s;
}
div[data-testid="stButton"] button:hover {
  filter: brightness(1.1); transform: translateY(-1px); border-color: #3a4568 !important;
}
div[data-testid="stButton"] button:active { transform: translateY(0); }
div[data-testid="stButton"] button:disabled { opacity: .55; cursor: not-allowed; transform: none; }
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"],
div[data-testid="stFormSubmitButton"] button[data-testid="stBaseButton-primary"] {
  background: linear-gradient(160deg, var(--db-red), var(--db-red-dark)) !important;
  color: #fff !important; border-color: rgba(255,255,255,.14) !important;
  box-shadow: 0 8px 22px rgba(236,0,22,.38), inset 0 1px 0 rgba(255,255,255,.3) !important;
  text-transform: uppercase; letter-spacing: .06em; font-family: var(--cond) !important;
}
div[data-testid="stButton"] button[data-testid="stBaseButton-primary"]:hover {
  box-shadow: 0 10px 28px rgba(236,0,22,.5), inset 0 1px 0 rgba(255,255,255,.3) !important;
}
div[data-testid="stButton"] button[kind="secondary"] { background: #232b45 !important; }
.sn-icon-btn button { padding: 7px 12px !important; font-size: 13px !important; }

/* ===== tabla de conexiones ===== */
.sn-th {
  font-family: var(--cond); font-size: 11px; text-transform: uppercase; letter-spacing: .12em;
  color: var(--muted); text-align: left; padding: 8px 10px;
  border-bottom: 2px solid var(--border); white-space: nowrap;
}
.sn-td {
  padding: 11px 10px; border-bottom: 1px solid var(--border-soft);
  font-size: 14px; vertical-align: middle;
}
.sn-td .muted { color: var(--muted); font-size: 13px; }
.sn-td b { font-weight: 700; }
.sn-train-kind {
  display: inline-block; font-family: var(--cond); font-weight: 700; font-size: 11px;
  letter-spacing: .06em; padding: 3px 9px; border-radius: 6px; text-transform: uppercase;
  white-space: nowrap; background: linear-gradient(160deg, var(--db-red), var(--db-red-dark));
  color: #fff; box-shadow: 0 3px 10px rgba(236,0,22,.35);
}
.sn-train-kind.local {
  background: linear-gradient(160deg, #2563eb, #1e40af); box-shadow: 0 3px 10px rgba(37,99,235,.35);
}
.sn-train-no {
  font-family: var(--mono); font-size: 14px; font-weight: 700;
  color: #ffd0d4; margin-left: 9px; letter-spacing: .05em; white-space: nowrap;
}
.sn-train-no small {
  font-family: var(--sans); font-size: 10px; font-weight: 600;
  letter-spacing: .12em; text-transform: uppercase; color: var(--muted);
  margin-right: 5px; vertical-align: 1px;
}
.sn-arrow { color: var(--db-red); font-weight: 700; margin: 0 6px; }
.sn-free { color: var(--green); font-weight: 700; }
.sn-open-btn button {
  background: linear-gradient(160deg, var(--db-red), var(--db-red-dark)) !important;
  color: #fff !important; border: none !important; padding: 7px 16px !important;
  font-family: var(--cond) !important; text-transform: uppercase; letter-spacing: .06em !important;
  font-size: 13px !important; border-radius: 9px !important;
  box-shadow: 0 6px 16px rgba(236,0,22,.35) !important;
}

/* ===== cabecera de viaje ===== */
.sn-trip-top {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
}
.sn-trip-title {
  font-family: var(--cond); font-size: 24px; font-weight: 700; margin: 0; letter-spacing: .01em;
}
.sn-trip-meta { color: var(--muted); font-size: 13.5px; margin-top: 4px; }
.sn-countdown {
  font-family: var(--mono); font-size: 40px; font-weight: 700; line-height: 1;
  color: var(--amber); background: #0c0f1c;
  border: 2px solid rgba(245,163,0,.6); border-radius: 14px;
  padding: 10px 20px; letter-spacing: .06em;
  font-variant-numeric: tabular-nums; white-space: nowrap; text-align: center;
  box-shadow: 0 0 26px rgba(245,163,0,.18), inset 0 0 18px rgba(245,163,0,.06);
}
.sn-countdown.warn {
  color: #ff7a85; border-color: var(--red);
  box-shadow: 0 0 26px rgba(239,68,68,.25); animation: sn-blink 1s steps(2) infinite;
}
@keyframes sn-blink { 50% { opacity: .55; } }

/* ===== panel de informatización (live) ===== */
.sn-live {
  display: flex; gap: 12px; align-items: flex-start;
  margin-top: 16px; padding: 14px 16px;
  background: #12100a; border: 1px solid #5c4a12; border-radius: 12px;
  color: #ffe082; font-size: 14px;
  box-shadow: inset 0 0 26px rgba(245,163,0,.06);
}
.sn-live.warn { border-color: #7a5c14; background: #17130a; color: #ffd08a; }
.sn-live.err { border-color: #7f1d1d; background: #170b0b; color: #ffb3ba; }
.sn-live .sn-dot {
  width: 11px; height: 11px; border-radius: 50%; background: var(--green);
  margin-top: 4px; flex: 0 0 auto; animation: sn-pulse 1.6s infinite;
}
.sn-live.warn .sn-dot { background: var(--amber); animation: none; box-shadow: none; }
.sn-live.err .sn-dot { background: var(--red); animation: none; }
@keyframes sn-pulse {
  0% { box-shadow: 0 0 0 0 rgba(33,193,122,.55); }
  70% { box-shadow: 0 0 0 9px rgba(33,193,122,0); }
  100% { box-shadow: 0 0 0 0 rgba(33,193,122,0); }
}
.sn-live .sn-kv { padding: 2px 0; }
.sn-live .sn-kv b { color: #c9b065; }
.sn-live small { color: #a8944f; font-family: var(--mono); font-size: 11.5px; }
.sn-live .sn-next { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.sn-live .sn-next span {
  font-family: var(--mono); font-size: 12px; background: #1d1908; border: 1px solid #4a3c10;
  padding: 3px 9px; border-radius: 6px; color: #d8c37e;
}
.sn-live .sn-next span.delayed { color: var(--amber); border-color: #7a5c14; }

/* ===== chips de paradas ===== */
.sn-chips { display: flex; gap: 7px; flex-wrap: wrap; margin: 6px 0 4px; }
.sn-chip {
  font-size: 12.5px; padding: 5px 11px; border-radius: 999px;
  background: var(--panel2); border: 1px solid var(--border); color: var(--muted);
  transition: color .15s, border-color .15s;
}
.sn-chip.focus {
  color: #fff; border-color: rgba(236,0,22,.65); font-weight: 700;
  background: linear-gradient(160deg, rgba(236,0,22,.9), var(--db-red-dark));
  box-shadow: 0 4px 14px rgba(236,0,22,.35);
}
.sn-chip .sn-tf {
  font-size: 11px; font-weight: 700; color: #38bdf8;
  background: rgba(56,189,248,.12); border: 1px solid rgba(56,189,248,.35);
  border-radius: 999px; padding: 1px 8px; margin-left: 4px;
}

/* ===== leyenda ===== */
.sn-legend { display: flex; gap: 16px; font-size: 12.5px; color: var(--muted); margin: 14px 0; flex-wrap: wrap; }
.sn-legend span { display: inline-flex; align-items: center; gap: 7px; }
.sn-swatch {
  width: 13px; height: 13px; border-radius: 4px; display: inline-block;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.18);
}

/* ===== vagones ===== */
.sn-wagon {
  margin-top: 14px; padding: 0 0 16px;
  background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
  border: 1px solid var(--border); border-radius: 16px;
  position: relative; overflow: hidden;
}
.sn-wagon::after {
  content: ''; position: absolute; left: 14px; right: 14px; bottom: 6px; height: 4px;
  background: repeating-linear-gradient(90deg, #2b3352 0 18px, transparent 18px 30px);
  opacity: .6; border-radius: 4px;
}
.sn-wagon-head {
  font-family: var(--cond); font-size: 13px; letter-spacing: .1em; text-transform: uppercase;
  color: #ffd0d4; margin: 0; padding: 11px 16px;
  background: linear-gradient(90deg, rgba(236,0,22,.22), transparent 70%);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
}
.sn-wagon-head::before {
  content: ''; width: 8px; height: 8px; border-radius: 2px; background: var(--db-red);
  box-shadow: 0 0 8px rgba(236,0,22,.8);
}
.sn-seats {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(52px, 1fr));
  gap: 9px; padding: 14px 16px 22px;
}
/* contenedores Streamlit que hacen de vagón y de rejilla de asientos */
div[class*="st-key-wagon_"] {
  margin-top: 14px; padding: 0 0 16px;
  background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
  border: 1px solid var(--border) !important; border-radius: 16px !important;
  position: relative; overflow: hidden;
}
div[class*="st-key-wagon_"]::after {
  content: ''; position: absolute; left: 14px; right: 14px; bottom: 6px; height: 4px;
  background: repeating-linear-gradient(90deg, #2b3352 0 18px, transparent 18px 30px);
  opacity: .6; border-radius: 4px;
}
div[class*="st-key-snseats_"] { padding: 14px 16px 22px; max-width: 380px; }
div[class*="st-key-snseats_"] [data-testid="stHorizontalBlock"] { gap: 9px; }

/* ===== asientos (botones con forma de butaca) =====
   Streamlit impone `div[data-testid="stButton"] button[kind="secondary"]`
   con `background ... !important`, asi que los selectores de los asientos
   anaden ese atributo para ganar en especificidad. */
div[data-testid="stElementContainer"][class*="st-key-seat_"] button[kind="secondary"],
div[data-testid="stElementContainer"][class*="st-key-seat_"] button[data-testid="stBaseButton-secondary"] {
  aspect-ratio: 1 !important;
  border-radius: 12px 12px 7px 7px !important;
  border: 1px solid rgba(255,255,255,.16) !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  font-family: var(--mono) !important; font-size: 12.5px !important; font-weight: 700 !important;
  padding: 0 !important; min-height: 0 !important; position: relative;
  transition: transform .12s, filter .12s, box-shadow .15s;
}
div[class*="st-key-seat_"] button::after {
  content: ''; position: absolute; inset: auto 4px 3px; height: 3px; border-radius: 3px;
  background: rgba(0,0,0,.22);
}
div[class*="st-key-seat_"] button:hover {
  transform: translateY(-3px) scale(1.05); filter: brightness(1.08);
}
div[class*="st-key-seat_"] button:disabled { cursor: not-allowed; transform: none; }

/* ===== panel de reserva / modal ===== */
.sn-modal h2 {
  font-family: var(--cond); text-transform: uppercase; letter-spacing: .05em;
  font-size: 17px; margin: 0 0 12px;
}
.sn-kv { display: flex; justify-content: space-between; gap: 14px; padding: 6px 0; font-size: 13.5px; }
.sn-kv b { color: var(--muted); font-weight: 400; text-align: left; }
.sn-kv span { text-align: right; font-weight: 500; }
.sn-qr {
  text-align: center; padding: 16px;
  background: #fff; border-radius: 12px; margin: 10px 0;
  border: 4px dashed #d7dbe6;
}
.sn-qr .muted { color: #6b7280; font-size: 12px; margin-bottom: 6px; }
.sn-qr code {
  font-family: var(--mono); font-size: 15px; letter-spacing: .1em; color: #0b0d14; font-weight: 700;
}
div[class*="st-key-qrbox_"] img {
  background: #fff; padding: 18px; border-radius: 12px;
  border: 4px dashed #d7dbe6; box-sizing: content-box;
}

/* ===== errores y avisos ===== */
.sn-error {
  background: rgba(239,68,68,.12); border: 1px solid #7f1d1d; color: #fca5a5;
  padding: 11px 15px; border-radius: 12px; font-size: 13.5px; margin-bottom: 14px;
}
.sn-hint { font-size: 12px; color: var(--muted); margin-top: 10px; }
.sn-hint b { color: #ff8f99; font-weight: 600; }

/* ===== pie ===== */
.sn-footer {
  max-width: 1080px; margin: 30px auto 40px;
  color: #5c6580; font-size: 12px;
  display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
}
.sn-footer .rail { color: var(--db-red); font-weight: 700; letter-spacing: .2em; }

/* ===== metricas ===== */
div[data-testid="stMetric"] {
  background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
  border: 1px solid var(--border); border-radius: 14px; padding: 12px 14px;
}
div[data-testid="stMetricLabel"] p {
  font-family: var(--cond); text-transform: uppercase; letter-spacing: .1em;
  font-size: 11px !important; color: var(--muted) !important;
}
div[data-testid="stMetricValue"] { font-family: var(--cond); font-size: 24px !important; }
div[data-testid="stMetricDelta"] { font-family: var(--sans); }

/* ===== barra de progreso / avisos ===== */
div[data-testid="stProgress"] > div > div > div > div {
  background: linear-gradient(90deg, var(--db-red), var(--db-red-dark));
}
div[data-testid="stAlert"] { border-radius: 14px; }

/* ===== responsivo ===== */
@media (max-width: 760px) {
  div[class*="st-key-sn_appbar"] { padding: 12px 14px; }
  .sn-title { font-size: 21px; white-space: normal; }
  .sn-trip-title { font-size: 19px; }
  .sn-countdown { font-size: 30px; }
}
"""


def inject() -> None:
    """Inyecta la hoja de estilos en el documento."""
    st.html(f"<style>{CSS.replace('__BRAND_SVG_URI__', BRAND_SVG_URI)}</style>")


# --------------------------------------------------------------------------- #
# Builders de HTML
# --------------------------------------------------------------------------- #

def brand_html(source: str = "demo") -> str:
    subtitle = (
        "Sitzplätze pro Teilstrecke · live Fahrpläne der Deutschen Bahn"
        if source == "db"
        else "Sitzplätze pro Teilstrecke · ICE/IC über Fulda · echte GTFS-Daten"
    )
    return (
        '<div class="sn-brand">'
        '<div class="sn-mark"></div>'
        '<h1 class="sn-title">SeatNow'
        f"<small>{subtitle}</small>"
        "</h1></div>"
    )


def badge(text: str, kind: str = "") -> str:
    cls = "sn-badge" + (f" {kind}" if kind else "")
    return f'<span class="{cls}">{escape(text)}</span>'


def badges_html(feed: Any, user: dict[str, Any] | None = None, mode: str = "demo") -> str:
    seats = CAR_COUNT * SEATS_PER_CAR
    if mode == "db":
        items = [
            badge("DB Live-Fahrplan", "ok"),
            badge(f"{len(feed.trips)} Züge"),
            badge(f"{len(feed.segments)} Teilstrecken"),
            badge(f"{seats} Sitzplätze"),
            badge("Verspätungen live"),
            badge(f"Zahlungen {PAYMENTS_MODE}"),
            badge(f"E-Mail {MAIL_MODE}", "warn"),
        ]
    else:
        items = [
            badge("GTFS Import", "ok"),
            badge(f"{len(feed.trips)} ICE/IC"),
            badge(f"{len(feed.segments)} Teilstrecken"),
            badge(f"{seats} Sitzplätze"),
            badge("1 Betriebstag"),
            badge(f"Zahlungen {PAYMENTS_MODE}"),
            badge(f"E-Mail {MAIL_MODE}", "warn"),
        ]
    if user:
        items.append(badge(f"Konto: {user['email']}", "ok"))
    else:
        items.append(badge("Konto: nicht angemeldet", "warn"))
    return f'<div class="sn-badges">{"".join(items)}</div>'


def meta_strip_html(feed: Any, source: str = "demo") -> str:
    """Franja de metadatos que antes vivía en la barra lateral."""
    seats = CAR_COUNT * SEATS_PER_CAR
    items = (
        (
            "Bestand",
            f"{len(feed.trips)} Züge · {len(feed.segments)} Teilstrecken · "
            f"{seats} Sitzplätze/Zug",
        ),
        (
            "Datenquelle",
            "DB-Live-Fahrplan" if source == "db" else "GTFS-Demofeed (Spanien)",
        ),
        ("Zahlung", f"{PAYMENTS_MODE} · E-Mail: {MAIL_MODE}"),
        ("Stand", f"{local_now():%d.%m.%Y %H:%M}"),
        ("Demo", "pasajero1@trenes.local / demo1234"),
    )
    parts = [
        f'<span class="sn-meta-item"><b>{escape(label)}</b>'
        f"<span>{escape(value)}</span></span>"
        for label, value in items
    ]
    return f'<div class="sn-meta">{"".join(parts)}</div>'


def header_html(feed: Any, user: dict[str, Any] | None = None, mode: str = "demo") -> str:
    """Cabecera: marcas de estado + franja de metadatos."""
    return f"{badges_html(feed, user, mode)}{meta_strip_html(feed, mode)}"


def card_title(text: str) -> str:
    return f'<p class="sn-card-title">{escape(text)}</p>'


def card_head(title: str, right: str = "") -> str:
    return f'<div class="sn-card-head">{card_title(title)}{right}</div>'


def train_kind_html(label: str) -> str:
    kind = "" if label.upper().startswith(("ICE", "IC", "AVE", "TGV")) else " local"
    return f'<span class="sn-train-kind{kind}">{escape(label)}</span>'


def train_html(product: str, number: str = "") -> str:
    """Producto + número de tren: 'ICE' + '799' -> 'ICE 799'."""
    badge_html = train_kind_html(product)
    if not number:
        return badge_html
    return f'{badge_html}<span class="sn-train-no">{escape(number)}</span>'


def train_number_html(number: str) -> str:
    """Número de tren suelto (para cabeceras y tablas)."""
    if not number:
        return ""
    return f'<span class="sn-train-no">{escape(number)}</span>'


def route_html(origin: str, destination: str) -> str:
    return (
        f"<b>{escape(origin)}</b>"
        '<span class="sn-arrow">➔</span>'
        f"<b>{escape(destination)}</b>"
    )


def time_html(departure: str, arrival: str) -> str:
    return f'<b>{escape(departure)}</b> <span class="muted">→</span> <b>{escape(arrival)}</b>'


def free_html(count: int) -> str:
    return f'<span class="sn-free">{count}</span> frei'


def chips_html(items: list[tuple[str, bool]]) -> str:
    parts = []
    for text, focus in items:
        cls = "sn-chip focus" if focus else "sn-chip"
        parts.append(f'<span class="{cls}">{escape(text)}</span>')
    return f'<div class="sn-chips">{"".join(parts)}</div>'


def legend_html() -> str:
    return (
        '<div class="sn-legend">'
        '<span><span class="sn-swatch" style="background:var(--green)"></span> frei</span>'
        '<span><span class="sn-swatch" style="background:var(--amber)"></span> reserviert (Hold)</span>'
        '<span><span class="sn-swatch" style="background:var(--red)"></span> belegt</span>'
        '<span class="muted">Klick auf ein freies Sitzplatz = Reservierung</span>'
        "</div>"
    )


def live_box_html(
    status: Any,
    next_stops: list[dict[str, Any]],
    updated_at: str,
) -> str:
    if status.cancelled:
        delay = '<b style="color:var(--red)">AUSGEFALLEN</b>'
    elif status.delay_minutes:
        delay = f'<b style="color:var(--amber)">Verspätung {status.delay_minutes} Min.</b>'
    else:
        delay = '<b style="color:var(--green)">pünktlich</b>'

    platform = ""
    if status.platform:
        platform = f'<div class="sn-kv"><b>Gleis</b><span>{escape(status.platform)}</span></div>'

    next_html = ""
    if next_stops:
        chips = []
        for stop in next_stops:
            delay_minutes = int(stop.get("delay_minutes") or 0)
            cls = "delayed" if delay_minutes else ""
            suffix = f" (+{delay_minutes})" if delay_minutes else ""
            chips.append(
                f'<span class="{cls}">{escape(str(stop["name"]))} '
                f'{escape(str(stop.get("departure") or stop.get("arrival") or ""))}{suffix}</span>'
            )
        next_html = f'<div class="sn-next">{"".join(chips)}</div>'

    klass = "sn-live"
    if status.cancelled:
        klass += " err"
    elif status.delay_minutes:
        klass += " warn"

    return (
        f'<div class="{klass}"><span class="sn-dot"></span><div style="flex:1">'
        f'<div class="sn-kv"><b>Status</b><span>{delay}</span></div>'
        f"{platform}"
        f"<small>Aktualisiert {escape(updated_at)}</small>"
        f"{next_html}"
        "</div></div>"
    )


def qr_box_html(payload: str) -> str:
    return (
        '<div class="sn-qr">'
        '<div class="muted">Code für den Check-in</div>'
        f"<code>{escape(payload)}</code>"
        "</div>"
    )


def footer_html(source: str = "demo") -> str:
    data = "Streamlit · SQLite · DB Timetables API" if source == "db" else "Streamlit · SQLite · GTFS"
    return (
        '<div class="sn-footer">'
        '<span><span class="rail">▚</span> SeatNow · Sitzplatzverwaltung pro Teilstrecke</span>'
        f"<span>{data}</span>"
        "</div>"
    )


def hint_html(text: str) -> str:
    return f'<div class="sn-hint">{text}</div>'


def error_html(text: str) -> str:
    return f'<div class="sn-error">{escape(text)}</div>'


# --------------------------------------------------------------------------- #
# Estilos dinamicos (dependen del estado en vivo)
# --------------------------------------------------------------------------- #

def seat_css(statuses: dict[str, str]) -> str:
    """CSS por asiento: verde libre, ambar retenido, rojo ocupado."""
    rules = []
    for key, status in statuses.items():
        selector = (
            f'div[data-testid="stElementContainer"][class*="st-key-{key}"] button[kind="secondary"],'
            f'div[data-testid="stElementContainer"][class*="st-key-{key}"] '
            'button[data-testid="stBaseButton-secondary"]'
        )
        if status == "AVAILABLE":
            rules.append(
                f"{selector}{{background:linear-gradient(160deg,#41e39b,var(--green)) !important;"
                "color:#07130c !important;"
                "box-shadow:0 5px 14px rgba(33,193,122,.30),inset 0 1px 0 rgba(255,255,255,.5) !important;}"
            )
        elif status == "HELD":
            rules.append(
                f"{selector}{{background:linear-gradient(160deg,#ffc44d,var(--amber)) !important;"
                "color:#241a02 !important;"
                "box-shadow:0 4px 12px rgba(245,163,0,.30) !important;}"
            )
        else:
            rules.append(
                f"{selector}{{background:repeating-linear-gradient(-45deg,var(--red) 0 8px,#c81e2c 8px 16px)"
                " !important;"
                "color:#fff !important;"
                "box-shadow:0 4px 12px rgba(239,68,68,.30) !important;}"
            )
    return f"<style>{''.join(rules)}</style>"
