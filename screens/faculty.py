# pages/faculty.py
from __future__ import annotations
import re
import pandas as pd
import streamlit as st

from core.db import get_conn, read_df, exec_sql, exec_many, ensure_base_schema
from core.theme import render_theme_css
from core.branding import render_header, render_footer
from core.security import create_user_for_faculty, create_user, ensure_users_for_all_faculty

def _ensure_schema():
    ensure_base_schema()

def _can_edit(role: str) -> bool:
    return (role or "").lower() in ("superadmin", "principal", "director")

_TITLES = re.compile(r"^(dr\.?|prof\.?|ar\.?|er\.?|architect|engineer|mr\.?|mrs\.?|ms\.?)\s+", re.I)
def _strip_title(name: str) -> str:
    return re.sub(_TITLES, "", str(name or "").strip()).strip()

FAC_TYPES = ["core", "visiting"]

def _designation_options():
    df = read_df("SELECT designation FROM faculty_designation_policy ORDER BY designation")
    return df["designation"].tolist() if not df.empty else ["Assistant Professor","Associate Professor","Professor"]

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue."); st.stop()

    _ensure_schema()
    render_theme_css()
    render_header()
    st.header("Faculty")

    editable = _can_edit(user.get("role",""))

    # Add faculty
    st.subheader("Add faculty")
    with st.form("add_faculty", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.6, 1, 1, 1])
        with c1:
            name_in = st.text_input("Name *", placeholder="e.g., Parikshit Waghdhare")
        with c2:
            ftype_in = st.selectbox("Type *", FAC_TYPES, index=0)
        with c3:
            desig_in = st.selectbox("Designation", _designation_options(), index=0)
        with c4:
            allow_in = st.number_input("Allowed credits (override)", min_value=0, max_value=100, value=0, help="0 = use designation policy")
        email_in = st.text_input("Email (optional)")
        ok_add = st.form_submit_button("Add", disabled=not editable)

    if ok_add:
        n = _strip_title(name_in)
        if not n:
            st.error("Name is required.")
        else:
            try:
                allow_val = int(allow_in) if allow_in else None
                exec_sql("INSERT INTO faculty(name, type, email, designation, allowed_credits) VALUES(?,?,?,?,?)",
                         (n, ftype_in, email_in.strip() or None, desig_in, allow_val))
                fid = int(read_df("SELECT id FROM faculty WHERE name=? ORDER BY id DESC LIMIT 1", (n,)).iloc[0]["id"])
                cred = create_user_for_faculty(fid, n)
                if cred:
                    st.success(f"Added **{n}** | {desig_in}. User: **{cred['username']}** / **{cred['temp_password']}**")
                else:
                    st.success(f"Added **{n}** | {desig_in}.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not add faculty: {e}")

    st.divider()

    # Import / Export
    st.subheader("Import / Export")
    cimp, cexp = st.columns(2)
    with cimp:
        up = st.file_uploader("Import faculty (CSV/XLSX)", type=["csv","xlsx","xls"], key="fac_imp")
        rep = st.checkbox("Replace all (dangerous)")
        if up and editable:
            try:
                if up.name.lower().endswith((".xlsx",".xls")):
                    df = pd.read_excel(up, dtype=str)
                else:
                    df = pd.read_csv(up, dtype=str)
                if df.empty:
                    st.error("File is empty.")
                else:
                    df.columns = [str(c).strip().lower() for c in df.columns]
                    get = lambda r,k: str(r.get(k,"")).strip()
                    rows = []
                    for _, r in df.iterrows():
                        nm = _strip_title(get(r,"name") or get(r,"faculty") or get(r,"faculty name"))
                        if not nm: continue
                        tp = (get(r,"type") or "core").lower(); tp = tp if tp in FAC_TYPES else "core"
                        em = get(r,"email") or None
                        ds = get(r,"designation") or None
                        al = get(r,"allowed_credits") or ""
                        alv = int(al) if al.isdigit() else None
                        un = get(r,"username"); pw = get(r,"password")
                        rows.append((nm,tp,em,ds,alv,un,pw))
                    if rep:
                        exec_sql("DELETE FROM users WHERE faculty_id IS NOT NULL")
                        exec_sql("DELETE FROM faculty_degree")
                        exec_sql("DELETE FROM faculty_roles")
                        exec_sql("DELETE FROM faculty")
                    exec_many("INSERT INTO faculty(name,type,email,designation,allowed_credits) VALUES(?,?,?,?,?)",
                              [(n,t,e,d,a) for (n,t,e,d,a,_,_) in rows])
                    # explicit creds
                    fdf = read_df("SELECT id, name FROM faculty")
                    name2id = {str(x["name"]).strip(): int(x["id"]) for _, x in fdf.iterrows()}
                    for (n,t,e,d,a,u,p) in rows:
                        if u and p:
                            try:
                                create_user(u,p,"subject_faculty","new",name2id.get(n),overwrite_password=True)
                            except Exception: pass
                    created = ensure_users_for_all_faculty("subject_faculty")
                    st.success(f"Imported {len(rows)}. Auto users: {len(created)}")
                    st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    with cexp:
        df = read_df("""
            SELECT f.id, f.name AS "Faculty", COALESCE(f.type,'') AS "Type",
                   COALESCE(f.designation,'') AS "Designation",
                   COALESCE(f.allowed_credits,'') AS "Allowed Credits (override)",
                   COALESCE(f.email,'') AS "Email",
                   COALESCE(u.username,'') AS "Username", COALESCE(u.role,'') AS "User Role"
            FROM faculty f
            LEFT JOIN users u ON u.faculty_id=f.id
            ORDER BY f.name
        """)
        st.download_button("Export faculty (CSV)",
                           data=(df.drop(columns=["id"],errors="ignore")).to_csv(index=False).encode("utf-8"),
                           file_name="faculty.csv", mime="text/csv")

    st.divider()

    # Browse/Edit list
    st.subheader("Faculty list")
    dfb = read_df("""
        SELECT f.id, f.name AS "Faculty", COALESCE(f.type,'') AS "Type",
               COALESCE(f.designation,'') AS "Designation",
               COALESCE(f.allowed_credits,'') AS "Allowed Credits (override)",
               COALESCE(f.email,'') AS "Email",
               COALESCE(u.username,'') AS "Username"
        FROM faculty f
        LEFT JOIN users u ON u.faculty_id=f.id
        ORDER BY f.name
    """)
    if dfb.empty:
        st.info("No faculty yet.")
    else:
        st.dataframe(dfb.drop(columns=["id"]), use_container_width=True)
        with st.expander("Edit designation / allowed credits"):
            ids = read_df("SELECT id, name FROM faculty ORDER BY name")
            if ids.empty:
                st.info("No faculty.")
            else:
                pick = st.selectbox("Pick faculty", ids["name"].tolist(), index=0)
                fid = int(ids[ids["name"]==pick]["id"].iloc[0])
                cur = read_df("SELECT designation, allowed_credits FROM faculty WHERE id=?", (fid,))
                des = st.selectbox("Designation", _designation_options(), index=0 if cur.empty else
                                   _designation_options().index(str(cur.iloc[0]["designation"] or _designation_options()[0]))
                                   if not cur.empty and str(cur.iloc[0]["designation"] or "") in _designation_options() else 0)
                alv = st.number_input("Allowed credits (override)", min_value=0, max_value=100,
                                      value=int(cur.iloc[0]["allowed_credits"]) if not cur.empty and str(cur.iloc[0]["allowed_credits"]).isdigit()
                                      else 0, help="0 = use designation policy")
                if st.button("Save", disabled=not editable):
                    try:
                        exec_sql("UPDATE faculty SET designation=?, allowed_credits=? WHERE id=?",
                                 (des, int(alv) if alv else None, fid))
                        st.success("Saved."); st.rerun()
                    except Exception as e:
                        st.error(f"Update failed: {e}")

    st.divider()
    st.caption("Director and Principal Roles by SuperAdmin.")
    st.caption("Subject role details (In-Charge / Faculty) are assigned on the Subject Allocation Page.")
    st.caption("Class in charge roles on Branches Page")
    render_footer()
