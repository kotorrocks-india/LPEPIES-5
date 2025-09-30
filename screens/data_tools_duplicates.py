# screens/data_tools_duplicates.py
from __future__ import annotations
import pandas as pd
import streamlit as st
from core.db import read_df, exec_sql
from core.theme import render_theme_css
from core.branding import render_header, render_footer

def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in str(s or "") if ch.isalnum())

def _dupe_block(title: str, df: pd.DataFrame, key_cols: list[str], id_col: str, table: str):
    st.markdown(f"### {title}")
    if df.empty:
        st.success("No rows found.")
        return
    # group by normalized keys
    df = df.copy()
    for k in key_cols:
        df[f"_{k}_norm"] = df[k].apply(_norm)
    grp = df.groupby([f"_{k}_norm" for k in key_cols], dropna=False)
    dup_keys = [g for g, sub in grp if len(sub) > 1]
    if not dup_keys:
        st.success("No duplicates detected.")
        return

    for i, g in enumerate(dup_keys, 1):
        sub = grp.get_group(g).sort_values(id_col)
        st.write(f"**Group {i}** — potential duplicates:")
        st.dataframe(sub, use_container_width=True)
        ids = sub[id_col].tolist()
        delete_ids = st.multiselect("Select IDs to delete (keep at least one!)", ids, key=f"{title}_mkdel_{i}")
        if st.button("Delete selected", key=f"{title}_btn_{i}") and delete_ids:
            try:
                for did in delete_ids:
                    exec_sql(f"DELETE FROM {table} WHERE {id_col}=?", (int(did),))
                st.success(f"Deleted {len(delete_ids)} row(s).")
                st.experimental_rerun()  # Streamlit <1.31; if >=1.31 use st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in."); st.stop()

    render_theme_css()
    render_header()
    st.header("Data Tools — Duplicates")

    role = (user.get("role") or "").lower()
    if role not in {"superadmin","principal","
