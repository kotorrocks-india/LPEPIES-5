# pages/pos.py
import pandas as pd, streamlit as st
from core.db import read_df, get_conn
from core.theme import render_theme_css
from core.branding import render_header, render_footer
from core.utils import df_to_csv_bytes

# ---------------- permissions ----------------
def can_manage(role):
    return role in ("superadmin","principal","director")

# ---------------- light, safe migrations ----------------
def _ensure_schema():
    with get_conn() as conn:
        cur = conn.cursor()
        # degrees table (if not already created elsewhere)
        cur.execute("""CREATE TABLE IF NOT EXISTS degrees(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )""")
        # degree-scoped POs
        cur.execute("""CREATE TABLE IF NOT EXISTS pos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            degree_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            UNIQUE(degree_id, code)
        )""")
        conn.commit()

# ---------------- page ----------------
def render(user):
    _ensure_schema()
    render_theme_css()
    render_header()
    st.header("Program Outcomes (POs)")

    role = user.get("role","")
    if not can_manage(role):
        st.info("Only Superadmin / Principal / Director can edit POs. Others have read-only access.")

    # Degree picker
    degs = read_df("SELECT id, name FROM degrees ORDER BY name")
    if degs.empty:
        st.warning("No degrees defined yet. Add one in **Degrees / Programs**.")
        render_footer()
        return

    deg_names = degs["name"].tolist()
    sel_deg = st.selectbox("Degree / Program", deg_names, index=0)
    sel_deg_id = int(degs[degs["name"]==sel_deg]["id"].iloc[0])

    # Current POs
    st.subheader(f"POs — {sel_deg}")
    df = read_df("SELECT id, code, COALESCE(name,'') AS name FROM pos WHERE degree_id=? ORDER BY id", (sel_deg_id,))
    if df.empty:
        st.caption("No POs yet. Add some below or import from CSV.")
        df = pd.DataFrame(columns=["id","code","name"])
    st.dataframe(df, use_container_width=True)

    # Export / Import
    colA, colB = st.columns(2)
    with colA:
        st.download_button(
            "Export POs (CSV)",
            data=df_to_csv_bytes(read_df("SELECT code,name FROM pos WHERE degree_id=? ORDER BY id", (sel_deg_id,))),
            file_name=f"pos_{sel_deg.replace(' ','_')}.csv",
            mime="text/csv"
        )

    with colB:
        up = st.file_uploader("Import POs (CSV with columns: code,name)", type=["csv"], disabled=not can_manage(role))
        if up is not None and can_manage(role):
            try:
                imp = pd.read_csv(up).rename(columns=lambda c: c.strip().lower())
                if "code" not in imp.columns:
                    st.error("CSV must include a 'code' column.")
                else:
                    # normalize codes like PO1, PO2...
                    imp["code"] = imp["code"].astype(str).str.strip()
                    if "name" not in imp.columns:
                        imp["name"] = ""
                    with get_conn() as conn:
                        cur = conn.cursor()
                        # Replace strategy: clear and insert (safer to keep order)
                        cur.execute("DELETE FROM pos WHERE degree_id=?", (sel_deg_id,))
                        for _, r in imp.iterrows():
                            code = str(r["code"]).strip()
                            name = str(r.get("name") or "").strip()
                            if not code:
                                continue
                            cur.execute("INSERT OR IGNORE INTO pos(degree_id,code,name) VALUES(?,?,?)",
                                        (sel_deg_id, code, name))
                        conn.commit()
                    st.success(f"Imported {len(imp)} PO rows for {sel_deg}.")
                    st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    st.divider()

    # Quick add / update (upsert by code)
    st.subheader("Add / Update a PO")
    with st.form("po_add_update", clear_on_submit=True):
        code = st.text_input("PO Code (e.g., PO1)").strip()
        name = st.text_input("PO Title / Description").strip()
        ok = st.form_submit_button("Save", disabled=not can_manage(role))
    if ok and can_manage(role):
        if not code:
            st.warning("Please enter a PO code.")
        else:
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    # upsert by (degree_id, code)
                    cur.execute("DELETE FROM pos WHERE degree_id=? AND code=?", (sel_deg_id, code))
                    cur.execute("INSERT INTO pos(degree_id, code, name) VALUES(?,?,?)", (sel_deg_id, code, name))
                    conn.commit()
                st.success(f"Saved {code}.")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

    # Optional: bulk editor (inline table)
    st.subheader("Bulk Edit (inline)")
    st.caption("Edit the table and click **Apply Changes** to upsert by PO code.")
    edit_df = read_df("SELECT code, COALESCE(name,'') AS name FROM pos WHERE degree_id=? ORDER BY id", (sel_deg_id,))
    edit_df = st.data_editor(
        edit_df if not edit_df.empty else pd.DataFrame(columns=["code","name"]),
        num_rows="dynamic",
        use_container_width=True,
        disabled=not can_manage(role),
        key="po_inline_editor"
    )
    if st.button("Apply Changes", disabled=not can_manage(role)):
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM pos WHERE degree_id=?", (sel_deg_id,))
                for _, r in edit_df.iterrows():
                    code = str(r.get("code") or "").strip()
                    if not code:
                        continue
                    name = str(r.get("name") or "").strip()
                    cur.execute("INSERT OR IGNORE INTO pos(degree_id,code,name) VALUES(?,?,?)",
                                (sel_deg_id, code, name))
                conn.commit()
            st.success("POs updated.")
            st.rerun()
        except Exception as e:
            st.error(f"Update failed: {e}")

    # Delete one PO
    st.subheader("Delete a PO")
    existing_codes = read_df("SELECT code FROM pos WHERE degree_id=? ORDER BY id", (sel_deg_id,))["code"].tolist()
    if not existing_codes:
        st.caption("No POs to delete.")
    else:
        del_code = st.selectbox("Select PO code to delete", existing_codes, index=0)
        if st.button("Delete PO", type="primary", disabled=not can_manage(role)):
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM pos WHERE degree_id=? AND code=?", (sel_deg_id, del_code))
                    conn.commit()
                st.success(f"Deleted {del_code}.")
                st.rerun()
            except Exception as e:
                st.error(f"Delete failed: {e}")

    render_footer()
