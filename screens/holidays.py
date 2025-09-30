# screens/holidays.py
from __future__ import annotations
import pandas as pd
import streamlit as st
from datetime import date

from core.db import read_df, exec_sql, exec_many, ensure_base_schema
from core.theme import render_theme_css
from core.branding import render_header, render_footer

# optional helper: CSV bytes
try:
    from core.utils import df_to_csv_bytes  # if you already have it
except Exception:
    def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
        return df.to_csv(index=False).encode("utf-8")

ALLOWED_EDIT_ROLES = {"superadmin", "principal", "director", "class_in_charge"}

def _ensure_tables():
    ensure_base_schema()  # includes holidays table and unique index

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    role = (user.get("role") or "").lower()
    can_edit = role in ALLOWED_EDIT_ROLES

    _ensure_tables()
    render_theme_css()
    render_header()

    st.header("Holidays")

    # Current list
    df_all = read_df("SELECT date AS Date, title AS Holiday FROM holidays ORDER BY date")
    st.dataframe(df_all if not df_all.empty else pd.DataFrame(columns=["Date", "Holiday"]),
                 use_container_width=True)

    st.divider()
    st.subheader("Add a holiday")
    col1, col2, col3 = st.columns([1.2, 2.5, 1])
    with col1:
        d = st.date_input("Date", value=date.today(), disabled=not can_edit)
    with col2:
        t = st.text_input("Name of holiday", value="", placeholder="e.g., Republic Day",
                          disabled=not can_edit)
    with col3:
        if st.button("Add", use_container_width=True, disabled=not can_edit):
            if not t.strip():
                st.error("Please enter a holiday name.")
            else:
                try:
                    # Unique (date, title) — ignore duplicates silently
                    exec_sql("INSERT OR IGNORE INTO holidays(date, title) VALUES(?,?)",
                             (d.isoformat(), t.strip()))
                    st.success("Added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Add failed: {e}")

    st.divider()
    st.subheader("Bulk import / export")

    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("Import Holidays (CSV with columns: date, title)", type=["csv"], disabled=not can_edit)
        if up is not None and st.button("Import now", use_container_width=True, disabled=not can_edit):
            try:
                df_in = pd.read_csv(up)
                # normalize columns
                df_in.columns = [c.strip().lower() for c in df_in.columns]
                if "date" not in df_in.columns or "title" not in df_in.columns:
                    st.error("CSV must have 'date' and 'title' columns.")
                else:
                    # Clean rows
                    rows = []
                    for _, r in df_in.iterrows():
                        ds = str(r.get("date", "")).strip()
                        ts = str(r.get("title", "")).strip()
                        if not ds or not ts:
                            continue
                        # try coercing to iso date if needed
                        try:
                            ds = pd.to_datetime(ds).date().isoformat()
                        except Exception:
                            # leave as-is; DB will reject invalid dates if using stricter checks later
                            pass
                        rows.append((ds, ts))
                    if rows:
                        exec_many("INSERT OR IGNORE INTO holidays(date, title) VALUES(?,?)", rows)
                        st.success(f"Imported {len(rows)} holidays.")
                        st.rerun()
                    else:
                        st.info("No valid rows found.")
            except Exception as e:
                st.error(f"Import failed: {e}")

    with c2:
        df_all2 = read_df("SELECT date AS Date, title AS Holiday FROM holidays ORDER BY date")
        st.download_button(
            "Export holidays (CSV)",
            data=df_to_csv_bytes(df_all2 if not df_all2.empty else pd.DataFrame(columns=["Date","Holiday"])),
            file_name="holidays.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()
    st.subheader("Remove duplicate rows (legacy)")
    st.caption("If your older database allowed duplicates, this removes exact duplicates keeping the earliest row.")
    if st.button("Remove duplicates", disabled=not can_edit):
        try:
            # Delete duplicates where (date, title) repeats; keep the lowest rowid
            exec_sql("""
                DELETE FROM holidays
                 WHERE rowid NOT IN (
                   SELECT MIN(rowid) FROM holidays GROUP BY date, title
                 )
            """)
            st.success("Duplicates removed (if any).")
            st.rerun()
        except Exception as e:
            st.error(f"Cleanup failed: {e}")

    st.divider()
    st.subheader("Delete a holiday")
    if not can_edit:
        st.info("Only Superadmin, Principal, Director, or Class In-Charge can delete holidays.")
    else:
        df_all3 = read_df("SELECT date, title FROM holidays ORDER BY date")
        if df_all3.empty:
            st.info("No holidays to delete.")
        else:
            label_map = [f"{r['date']} — {r['title']}" for _, r in df_all3.iterrows()]
            pick = st.selectbox("Pick a holiday to delete", label_map, index=0)
            if st.button("Delete", use_container_width=True):
                try:
                    ridx = label_map.index(pick)
                    dstr = str(df_all3.iloc[ridx]["date"])
                    tstr = str(df_all3.iloc[ridx]["title"])
                    exec_sql("DELETE FROM holidays WHERE date=? AND title=?", (dstr, tstr))
                    st.success("Deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

    render_footer()
