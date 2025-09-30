# pages/degrees.py  (schema-free; relies on core/db.py)
import pandas as pd, streamlit as st
from core.db import read_df, get_conn, ensure_base_schema
from core.theme import render_theme_css
from core.branding import render_header, render_footer
from core.utils import df_to_csv_bytes

def can_manage(role):
    return (role or "").lower() in ("superadmin","principal","director")

def render(user):
    ensure_base_schema()          # centralize all schema creation/migrations in core/db.py
    render_theme_css(); render_header()
    st.header("Degrees / Programs")

    role = (user or {}).get("role","")

    # List degrees
    df = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    st.subheader("Existing Degrees")
    show = df.rename(columns={"duration_years":"Duration (years)"}) if not df.empty else pd.DataFrame(columns=["id","name","Duration (years)"])
    st.dataframe(show, use_container_width=True)

    # Export / Import
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Export degrees (CSV)",
            data=df_to_csv_bytes(read_df("SELECT name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")),
            file_name="degrees.csv",
            mime="text/csv"
        )
    with col2:
        up = st.file_uploader("Import degrees (CSV: name,duration_years)", type=["csv"], disabled=not can_manage(role))
        if up is not None and can_manage(role):
            try:
                imp = pd.read_csv(up).rename(columns=lambda c: c.strip().lower())
                if "name" not in imp.columns:
                    st.error("CSV must include a 'name' column.")
                else:
                    if "duration_years" not in imp.columns:
                        imp["duration_years"] = 5
                    with get_conn() as conn:
                        cur = conn.cursor()
                        for _, r in imp.iterrows():
                            nm = str(r["name"]).strip()
                            if not nm: continue
                            try:
                                yrs = int(r.get("duration_years") or 5)
                            except Exception:
                                yrs = 5
                            cur.execute("INSERT OR IGNORE INTO degrees(name, duration_years) VALUES(?,?)", (nm, yrs))
                            cur.execute("UPDATE degrees SET duration_years=? WHERE name=?", (yrs, nm))
                        conn.commit()
                    st.success("Degrees imported/updated.")
                    st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    # Add / edit degree
    st.subheader("Add Degree")
    with st.form("deg_add", clear_on_submit=True):
        nm = st.text_input("Degree name (e.g., B Arch)").strip()
        yrs = st.number_input("Duration (years)", min_value=1, max_value=10, value=5, step=1)
        ok = st.form_submit_button("Add", disabled=not can_manage(role))
    if ok and can_manage(role):
        if not nm:
            st.warning("Please enter a degree name.")
        else:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("INSERT OR IGNORE INTO degrees(name, duration_years) VALUES(?,?)", (nm, int(yrs)))
                cur.execute("UPDATE degrees SET duration_years=? WHERE name=?", (int(yrs), nm))
                conn.commit()
            st.success(f"Added/updated: {nm} ({int(yrs)} years)")
            st.rerun()

    st.subheader("Manage Degree")
    df = read_df("SELECT id,name,COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    if df.empty:
        st.caption("No degrees yet."); render_footer(); return

    names = df["name"].tolist()
    sel = st.selectbox("Select degree", names, index=0)
    row = df[df["name"]==sel].iloc[0]
    deg_id = int(row["id"])
    cur_years = int(row["duration_years"])

    c1, c2, c3 = st.columns([2,1,1])
    with c1:
        new_name = st.text_input("Rename to", value=sel, key="deg_rename")
    with c2:
        new_years = st.number_input("Duration (years)", min_value=1, max_value=10, value=cur_years, step=1, key="deg_yrs_edit")
    with c3:
        if st.button("Save Changes", disabled=not can_manage(role)):
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE degrees SET name=?, duration_years=? WHERE id=?", (new_name.strip(), int(new_years), deg_id))
                    conn.commit()
                st.success("Saved.")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

    # Danger zone
    if st.checkbox("Yes, permanently delete this degree."):
        if st.button("Confirm Delete", type="primary", disabled=not can_manage(role)):
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM pos WHERE degree_id=?", (deg_id,))
                    cur.execute("DELETE FROM degrees WHERE id=?", (deg_id,))
                    conn.commit()
                st.success("Degree deleted.")
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")

    render_footer()
