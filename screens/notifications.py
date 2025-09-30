# screens/notifications.py
from __future__ import annotations
import pandas as pd
import streamlit as st
from core.db import read_df, exec_sql, ensure_base_schema
from core.theme import render_theme_css
from core.branding import render_header, render_footer

def _can_resolve(role: str) -> bool:
    return (role or "").lower() in ("principal","director","superadmin")

def _subject_label(sid: int) -> str:
    df = read_df("SELECT code,name FROM subjects WHERE id=?", (sid,))
    if df.empty: return f"Subject {sid}"
    r = df.iloc[0]
    return f"{r['code'] or ''} {r['name'] or ''}".strip()

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue."); st.stop()

    ensure_base_schema()
    render_theme_css()
    render_header()

    st.header("Notifications")

    can_resolve = _can_resolve(user.get("role",""))

    df = read_df("""
        SELECT id, created_at, type, subject_id, batch_year, semester, message, status, required_role
          FROM notifications
         ORDER BY (status='unread') DESC, created_at DESC
    """)

    if df.empty:
        st.info("No notifications.")
        render_footer(); return

    # Pretty
    df_show = df.copy()
    df_show["Subject"] = df_show["subject_id"].apply(_subject_label)
    df_show.rename(columns={
        "created_at":"Created",
        "batch_year":"Batch",
        "semester":"Sem",
        "status":"Status",
        "message":"Message"
    }, inplace=True)
    df_show = df_show[["Created","type","Subject","Batch","Sem","Message","Status"]]

    st.dataframe(df_show, use_container_width=True)

    # Resolve section
    st.subheader("Resolve")
    ids_unread = df[df["status"]=="unread"]["id"].astype(int).tolist()
    pick = st.selectbox("Pick notification to resolve", ["(none)"] + [str(i) for i in ids_unread], index=0)
    if st.button("Resolve selected", disabled=(not can_resolve or pick=="(none)")):
        try:
            nid = int(pick)
            exec_sql("UPDATE notifications SET status='resolved' WHERE id=?", (nid,))
            st.success("Notification resolved.")
            st.rerun()
        except Exception as e:
            st.error(f"Resolve failed: {e}")

    render_footer()
