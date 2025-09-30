# core/theme.py
from __future__ import annotations
import streamlit as st
from .db import read_df, get_conn

# -------------------------
# DB bootstrap (idempotent)
# -------------------------
def _ensure_theme_table():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS theme_settings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base TEXT,               -- "light" | "dark" (advisory)
                primary_color TEXT,
                accent_color TEXT,
                bg TEXT,                 -- page canvas
                text_color TEXT,
                card_bg TEXT,            -- forms/tables/expanders/tabs
                sidebar_bg TEXT,
                sidebar_text TEXT,
                header_bg TEXT,
                header_text TEXT,
                radius TEXT,             -- e.g. "12px"
                font_family TEXT
            )
        """)
        # best-effort migrations for old columns
        def has(col):
            cur.execute("PRAGMA table_info(theme_settings)")
            return col in {r[1] for r in cur.fetchall()}
        try:
            if has("primary") and not has("primary_color"):
                cur.execute("ALTER TABLE theme_settings ADD COLUMN primary_color TEXT")
                cur.execute("UPDATE theme_settings SET primary_color = primary WHERE primary_color IS NULL OR primary_color=''")
            if has("accent") and not has("accent_color"):
                cur.execute("ALTER TABLE theme_settings ADD COLUMN accent_color TEXT")
                cur.execute("UPDATE theme_settings SET accent_color = accent WHERE accent_color IS NULL OR accent_color=''")
            if has("text") and not has("text_color"):
                cur.execute("ALTER TABLE theme_settings ADD COLUMN text_color TEXT")
                cur.execute("UPDATE theme_settings SET text_color = text WHERE text_color IS NULL OR text_color=''")
        except Exception:
            pass

        cur.execute("SELECT COUNT(*) AS c FROM theme_settings")
        # FIX: fetchone() returns a tuple; use [0] instead of ["c"]
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO theme_settings(
                    base, primary_color, accent_color, bg, text_color, card_bg,
                    sidebar_bg, sidebar_text, header_bg, header_text, radius, font_family
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "light",
                "#2563eb",  # primary
                "#f59e0b",  # accent
                "#ffffff",  # page bg
                "#0f172a",  # text
                "#ffffff",  # card bg
                "#f1f5f9",  # sidebar bg
                "#0f172a",  # sidebar text
                "#ffffff",  # header bg
                "#0f172a",  # header text
                "12px",
                "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif",
            ))
        conn.commit()

def _get_theme_row() -> dict:
    _ensure_theme_table()
    df = read_df("SELECT * FROM theme_settings LIMIT 1")
    return {} if df.empty else df.iloc[0].to_dict()

# -------------------------------------------------
# Public: inject CSS variables + global styling
# -------------------------------------------------
def render_theme_css():
    t = _get_theme_row()
    base          = (t.get("base") or "light").strip()
    primary_color = (t.get("primary_color") or "#2563eb").strip()
    accent_color  = (t.get("accent_color") or "#f59e0b").strip()
    bg            = (t.get("bg") or "#ffffff").strip()
    text_color    = (t.get("text_color") or "#0f172a").strip()
    card_bg       = (t.get("card_bg") or "#ffffff").strip()
    sidebar_bg    = (t.get("sidebar_bg") or "#f1f5f9").strip()
    sidebar_text  = (t.get("sidebar_text") or "#0f172a").strip()
    header_bg     = (t.get("header_bg") or "#ffffff").strip()
    header_text   = (t.get("header_text") or "#0f172a").strip()
    radius        = (t.get("radius") or "12px").strip()
    font_family   = (t.get("font_family") or "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif").strip()

    st.markdown(
        f"""
<style>
:root {{
  --app-base: {base};
  --app-primary: {primary_color};
  --app-accent: {accent_color};
  --app-bg: {bg};
  --app-text: {text_color};
  --app-card-bg: {card_bg};
  --app-sidebar-bg: {sidebar_bg};
  --app-sidebar-text: {sidebar_text};
  --app-header-bg: {header_bg};
  --app-header-text: {header_text};
  --app-radius: {radius};
  --app-font: {font_family};
}}

html, body, .stApp {{
  background: var(--app-bg) !important;
  color: var(--app-text);
  font-family: var(--app-font);
}}

.block-container {{ padding-top: 1.2rem; }}

/* Header bar used by core.branding.render_header() */
.eplp-topbar {{
  background: var(--app-header-bg);
  color: var(--app-header-text);
  border-bottom: 1px solid rgba(0,0,0,0.06);
  padding: 10px 16px;
  border-radius: var(--app-radius) !important;
  margin-bottom: 10px;
}}
.eplp-topbar h1, .eplp-topbar h2, .eplp-topbar h3 {{
  color: var(--app-header-text) !important; margin: 0;
}}

/* Sidebar palette */
section[data-testid="stSidebar"] > div {{ background: var(--app-sidebar-bg); color: var(--app-sidebar-text); }}
section[data-testid="stSidebar"] * {{ color: var(--app-sidebar-text); }}

/* Tabs (headers only; panel body styled below) */
div.stTabs [data-baseweb="tab"] {{ border-radius: var(--app-radius) var(--app-radius) 0 0; }}

/* ---------- Buttons (consistent, accessible) ---------- */
.stButton > button,
.stDownloadButton > button,
.stForm button[type="submit"] {{
  background: var(--app-primary) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: var(--app-radius) !important;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  transition: filter .15s ease, transform .02s ease;
}}
.stButton > button:hover,
.stDownloadButton > button:hover,
.stForm button[type="submit"]:hover {{ filter: brightness(0.95); }}
.stButton > button:active,
.stDownloadButton > button:active,
.stForm button[type="submit"]:active {{ transform: translateY(1px); }}

/* Secondary/outline-style buttons */
.stButton > button[kind="secondary"],
.stForm button[kind="secondary"] {{
  background: #ffffff !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.25) !important;
}}

/* Disabled buttons */
.stButton > button:disabled,
.stDownloadButton > button:disabled,
.stForm button[type="submit"]:disabled {{
  background: rgba(0,0,0,0.15) !important;
  color: rgba(255,255,255,0.85) !important;
  border: none !important;
  opacity: 0.7 !important;
}}

/* File uploader "Browse" button + text */
div[data-testid="stFileUploader"] * {{ color: var(--app-text) !important; }}
div[data-testid="stFileUploader"] button {{
  background: var(--app-primary) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: var(--app-radius) !important;
}}

/* ---------- FORCE READABLE FORMS & CONTROLS (GLOBAL) ---------- */
label, .stMarkdown, .stMarkdown p, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
.stRadio label, .stCheckbox label, .stSelectbox label, .stNumberInput label,
.stTextInput label, .stFileUploader label, .stDateInput label {{
  color: var(--app-text) !important;
}}
.stTextInput input::placeholder,
.stTextArea textarea::placeholder,
.stNumberInput input::placeholder {{
  color: rgba(0,0,0,0.45) !important;
}}
.stTextInput input,
.stNumberInput input,
.stTextArea textarea {{
  background: #ffffff !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.18) !important;
  border-radius: var(--app-radius) !important;
}}
.stNumberInput button {{
  background: #ffffff !important;
  color: var(--app-text) !important;
  border-left: 1px solid rgba(0,0,0,0.18) !important;
}}
[data-baseweb="select"] div[role="button"] {{
  background: #ffffff !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.18) !important;
  border-radius: var(--app-radius) !important;
}}
[data-baseweb="select"] * {{
  color: var(--app-text) !important;
  fill: var(--app-text) !important;
}}
div[role="listbox"] {{
  background: #ffffff !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.18) !important;
  border-radius: var(--app-radius) !important;
}}
div[data-testid="stExpander"],
div[data-testid="stExpander"] > details,
div[data-testid="stExpander"] [data-testid="stExpanderContent"] {{
  background: var(--app-card-bg) !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.08) !important;
  border-radius: var(--app-radius) !important;
}}
.stForm {{
  background: var(--app-card-bg) !important;
  color: var(--app-text) !important;
  padding: 1rem;
  border-radius: var(--app-radius);
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  border: 1px solid rgba(0,0,0,0.06);
}}
div[data-testid="stDataFrame"] {{
  background: var(--app-card-bg) !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: var(--app-radius);
  padding: 6px;
}}
div[role="tabpanel"] {{
  background: var(--app-card-bg) !important;
  color: var(--app-text) !important;
  border: 1px solid rgba(0,0,0,0.06);
  border-top: none;
  border-radius: 0 0 var(--app-radius) var(--app-radius);
  padding: 10px;
}}
code, .stMarkdown code {{ background: transparent !important; color: inherit !important; padding: 0 !important; }}
div[data-testid="stAlert"] {{
  border-radius: var(--app-radius);
  border: 1px solid rgba(0,0,0,0.06);
}}
</style>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------------------------
# Update helpers
# -------------------------------------------------
def set_theme(
    *,
    base: str | None = None,
    primary_color: str | None = None,
    accent_color: str | None = None,
    bg: str | None = None,
    text_color: str | None = None,
    card_bg: str | None = None,
    sidebar_bg: str | None = None,
    sidebar_text: str | None = None,
    header_bg: str | None = None,
    header_text: str | None = None,
    radius: str | None = None,
    font_family: str | None = None,
):
    _ensure_theme_table()
    row = read_df("SELECT id FROM theme_settings LIMIT 1")
    if row.empty:
        return
    theme_id = int(row.iloc[0]["id"])

    fields = {
        "base": base,
        "primary_color": primary_color,
        "accent_color": accent_color,
        "bg": bg,
        "text_color": text_color,
        "card_bg": card_bg,
        "sidebar_bg": sidebar_bg,
        "sidebar_text": sidebar_text,
        "header_bg": header_bg,
        "header_text": header_text,
        "radius": radius,
        "font_family": font_family,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return

    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
    params = list(updates.values()) + [theme_id]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE theme_settings SET {set_clause} WHERE id=?", params)
        conn.commit()

# -------------------------------------------------
# Presets
# -------------------------------------------------
def apply_preset(name: str):
    n = (name or "").strip().lower()
    if n == "dark":
        set_theme(
            base="dark",
            primary_color="#60a5fa",
            accent_color="#f59e0b",
            bg="#0b1220",
            text_color="#e5e7eb",
            card_bg="#111827",
            sidebar_bg="#0f172a",
            sidebar_text="#e5e7eb",
            header_bg="#0f172a",
            header_text="#e5e7eb",
            radius="12px",
            font_family="Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif",
        )
    else:
        set_theme(
            base="light",
            primary_color="#2563eb",
            accent_color="#f59e0b",
            bg="#ffffff",
            text_color="#0f172a",
            card_bg="#ffffff",
            sidebar_bg="#f1f5f9",
            sidebar_text="#0f172a",
            header_bg="#ffffff",
            header_text="#0f172a",
            radius="12px",
            font_family="Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif",
        )
