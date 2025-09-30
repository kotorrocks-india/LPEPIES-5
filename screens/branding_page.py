# screens/branding_page.py
from __future__ import annotations
import base64
import streamlit as st

from core.db import read_df, get_conn
from core.theme import render_theme_css
from core.branding import render_header, render_footer, set_branding, safe_image

EDIT_ROLES = {"superadmin"}   # <— only superadmin can edit branding

def _ensure_branding():
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
        cur.execute("SELECT COUNT(*) AS c FROM branding")
        row = cur.fetchone()
        if (row[0] if row else 0) == 0:  # <-- FIX: tuple indexing
            cur.execute(
                "INSERT INTO branding(app_name, logo_url, login_bg, footer) VALUES(?,?,?,?)",
                ("EPLP/IES Manager", "", "", "© Your Name")
            )
        conn.commit()

def _get_branding() -> dict:
    _ensure_branding()
    df = read_df("SELECT * FROM branding LIMIT 1")
    return {} if df.empty else df.iloc[0].to_dict()

def _file_to_data_uri(uploaded_file) -> str:
    """
    Convert uploaded image to a data: URI so it survives restarts without a file server.
    """
    if not uploaded_file:
        return ""
    data = uploaded_file.read()
    mime = uploaded_file.type or "image/png"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    render_theme_css()
    render_header()
    st.header("Branding")

    role = (user.get("role") or "").lower()
    editable = role in EDIT_ROLES

    b = _get_branding()

    # Preview
    st.markdown("### Preview")
    cprev1, cprev2 = st.columns([1, 2])
    with cprev1:
        st.caption("Logo")
        if b.get("logo_url"):
            safe_image(b["logo_url"])
        else:
            st.info("No logo uploaded yet.")
    with cprev2:
        st.caption("Login background sample")
        if b.get("login_bg"):
            st.markdown(
                f"""
                <div style="height:180px;border:1px solid rgba(0,0,0,0.1);
                            border-radius:12px;background-image:url('{b['login_bg']}');
                            background-size:cover;background-position:center;"></div>
                """,
                unsafe_allow_html=True
            )
        else:
            st.info("No login background set yet.")

    st.divider()

    # Editor (superadmin only)
    st.markdown("### Edit branding")
    if not editable:
        st.info("Only Superadmin can edit branding (logo, login background, app name, footer).")
        render_footer()
        return

    with st.form("branding_form"):
        app_name = st.text_input("App name", value=str(b.get("app_name") or "EPLP/IES Manager"))
        footer   = st.text_input("Footer", value=str(b.get("footer") or "© Your Name"))

        c1, c2 = st.columns(2)
        with c1:
            up_logo = st.file_uploader("Upload logo (PNG/JPG/SVG)", type=["png", "jpg", "jpeg", "svg"])
            logo_url = b.get("logo_url") or ""
            if up_logo:
                logo_url = _file_to_data_uri(up_logo)
        with c2:
            up_bg = st.file_uploader("Upload login background (PNG/JPG)", type=["png", "jpg", "jpeg"])
            login_bg = b.get("login_bg") or ""
            if up_bg:
                login_bg = _file_to_data_uri(up_bg)

        ok = st.form_submit_button("Save branding")
    if ok:
        try:
            set_branding(
                app_name=app_name.strip(),
                logo_url=(logo_url or "").strip(),
                login_bg=(login_bg or "").strip(),
                footer=footer.strip()
            )
            st.success("Branding saved.")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")

    render_footer()
