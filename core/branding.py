# core/branding.py
from __future__ import annotations
import streamlit as st
from typing import Dict, Optional
from .db import get_conn, read_df

# =========================
# DB bootstrap / accessors
# =========================
def _ensure_branding_table():
    """Create branding table if missing and seed one row."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS branding(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT,
                logo_url TEXT,
                login_bg TEXT,
                footer TEXT
            )
        """)
        conn.commit()
        # seed a single row if empty
        cur.execute("SELECT COUNT(*) AS c FROM branding")
        row = cur.fetchone()
        if (row[0] if row else 0) == 0:
            cur.execute(
                "INSERT INTO branding(app_name, logo_url, login_bg, footer) VALUES(?,?,?,?)",
                ("EPLP/IES Manager", "", "", "© Your Name")
            )
            conn.commit()

@st.cache_data(show_spinner=False)
def _get_branding_cached() -> Dict:
    _ensure_branding_table()
    df = read_df("SELECT * FROM branding LIMIT 1")
    return {} if df.empty else df.iloc[0].to_dict()

def get_branding(refresh: bool = False) -> Dict:
    """Return branding row as dict. Use refresh=True after saving."""
    if refresh:
        _get_branding_cached.clear()  # type: ignore[attr-defined]
    return _get_branding_cached()

def set_branding(*, app_name: Optional[str] = None,
                 logo_url: Optional[str] = None,
                 login_bg: Optional[str] = None,
                 footer: Optional[str] = None) -> None:
    """Update any subset of branding fields (single-row table)."""
    _ensure_branding_table()
    fields = {
        "app_name": app_name,
        "logo_url": logo_url,
        "login_bg": login_bg,
        "footer": footer,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return
    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
    params = list(updates.values())
    with get_conn() as conn:
        cur = conn.cursor()
        # ensure row exists
        cur.execute("SELECT id FROM branding LIMIT 1")
        r = cur.fetchone()
        if r is None:
            cur.execute("INSERT INTO branding(app_name, logo_url, login_bg, footer) VALUES(?,?,?,?)",
                        ("EPLP/IES Manager", "", "", "© Your Name"))
        # now update
        cur.execute(f"UPDATE branding SET {set_clause} WHERE id=(SELECT id FROM branding LIMIT 1)", params)
        conn.commit()
    # bust cache
    _get_branding_cached.clear()  # type: ignore[attr-defined]

# =========================
# Media helpers
# =========================
def safe_image(src: str, *, caption: str | None = None, height: int | None = None) -> None:
    """
    Render an image given either a regular URL or a data: URI.
    Falls back to raw HTML if st.image fails.
    """
    s = (src or "").strip()
    if not s:
        return
    try:
        st.image(s, caption=caption, use_container_width=True, output_format="auto")
    except Exception:
        cap = f"<div style='font-size:12px;opacity:.8;margin-top:4px'>{caption}</div>" if caption else ""
        h = f"height:{height}px;" if height else "height:auto;"
        st.markdown(
            f"""
            <div style="display:flex;align-items:center">
              <img src="{s}" style="{h}max-width:100%;object-fit:contain;border-radius:8px;border:1px solid rgba(0,0,0,.06)"/>
            </div>
            {cap}
            """,
            unsafe_allow_html=True
        )

# =========================
# Header / Footer (used by app.py)
# =========================
def render_header():
    """Top header bar (logo + app name). Idempotent per run."""
    if st.session_state.get("_hdr_done"):
        return
    b = get_branding()
    app_name = str(b.get("app_name") or "EPLP/IES Manager")
    logo_url = str(b.get("logo_url") or "").strip()

    with st.container():
        st.markdown('<div class="eplp-topbar">', unsafe_allow_html=True)
        cols = st.columns([0.14, 0.86])  # logo / title
        with cols[0]:
            if logo_url:
                safe_image(logo_url, height=56)
        with cols[1]:
            st.markdown(f"<h2 style='margin:0'>{app_name}</h2>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.session_state["_hdr_done"] = True

def render_footer():
    """Bottom footer bar."""
    if st.session_state.get("_ftr_done"):
        return
    b = get_branding()
    footer = str(b.get("footer") or "").strip()
    if footer:
        st.markdown(
            f"""
            <div style="
                margin-top:24px;
                padding:10px 12px;
                border-top:1px solid rgba(0,0,0,0.06);
                color: var(--app-text);
                opacity:.9;
                text-align:center;">
              {footer}
            </div>
            """,
            unsafe_allow_html=True
        )
    st.session_state["_ftr_done"] = True

def get_login_background() -> str:
    """Return the login background image (data URI or URL), or empty string."""
    b = get_branding()
    return str(b.get("login_bg") or "")
