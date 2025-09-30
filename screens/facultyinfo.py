# screens/facultyinfo.py
from __future__ import annotations
import pandas as pd
import streamlit as st
from core.db import read_df, exec_sql, exec_many, ensure_base_schema
from core.theme import render_theme_css
from core.branding import render_header, render_footer

def _can_manage_policy(role: str) -> bool:
    return (role or "").lower() in ("superadmin","principal","director")

def _required_for_designation(desig: str) -> int:
    df = read_df("SELECT required_credits FROM faculty_designation_policy WHERE designation=?", (desig,))
    return int(df.iloc[0]["required_credits"]) if not df.empty else 0

def _faculty_summary(fid: int) -> dict:
    # Degrees
    deg = read_df("""
        SELECT d.name AS degree
          FROM faculty_degree fd
          JOIN degrees d ON d.id = fd.degree_id
         WHERE fd.faculty_id=?
         ORDER BY d.name
    """, (fid,))
    degrees = deg["degree"].tolist() if not deg.empty else []

    # Class in-charge (degree + year)
    cic = read_df("""
        SELECT d.name AS degree, fr.slot AS year
          FROM faculty_roles fr
          JOIN degrees d ON d.id = fr.slot2
         WHERE fr.role_name='class_incharge' AND fr.faculty_id=?
         ORDER BY d.name, fr.slot
    """, (fid,))
    class_incharge = [{"degree": r["degree"], "year": int(r["year"])} for _, r in cic.iterrows()] if not cic.empty else []

    # Subject roles counts
    roles = read_df("""
        SELECT role, COUNT(DISTINCT subject_id) AS n
          FROM subject_faculty_map
         WHERE faculty_id=?
         GROUP BY role
    """, (fid,))
    cnt_incharge = int(roles[roles["role"]=="in_charge"]["n"].iloc[0]) if not roles.empty and "in_charge" in roles["role"].tolist() else 0
    cnt_faculty  = int(roles[roles["role"]=="faculty"]["n"].iloc[0]) if not roles.empty and "faculty" in roles["role"].tolist() else 0

    # Credit total (distinct subjects they are part of)
    credits = read_df("""
        SELECT SUM(DISTINCT s.credits) AS total_credits
          FROM subjects s
          JOIN subject_faculty_map m ON m.subject_id = s.id
         WHERE m.faculty_id=?
    """, (fid,))
    total_credits = float(credits["total_credits"].iloc[0]) if not credits.empty and pd.notna(credits["total_credits"].iloc[0]) else 0.0

    return {
        "degrees": degrees,
        "class_incharge": class_incharge,
        "subjects_incharge": cnt_incharge,
        "subjects_faculty": cnt_faculty,
        "credit_total": total_credits,
    }

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue."); st.stop()

    ensure_base_schema()
    render_theme_css()
    render_header()
    st.header("Faculty Info & Credits")

    # Policy editor (designation -> required credits)
    if _can_manage_policy(user.get("role","")):
        st.subheader("Designation credit policy")
        pol = read_df("SELECT designation, required_credits FROM faculty_designation_policy ORDER BY designation")
        grid = st.data_editor(
            pol if not pol.empty else pd.DataFrame(columns=["designation","required_credits"]),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "designation": st.column_config.TextColumn(required=True),
                "required_credits": st.column_config.NumberColumn(min_value=0, max_value=100, step=1, required=True),
            },
            key="pol_ed",
        )
        if st.button("Save policy"):
            try:
                exec_sql("DELETE FROM faculty_designation_policy")
                rows = []
                for _, r in grid.iterrows():
                    ds = str(r.get("designation","")).strip()
                    rc = r.get("required_credits")
                    if ds and pd.notna(rc):
                        rows.append((ds, int(rc)))
                exec_many("INSERT INTO faculty_designation_policy(designation, required_credits) VALUES(?,?)", rows)
                st.success("Policy saved."); st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")
        st.divider()

    # Pick a faculty to view
    fdf = read_df("SELECT id, name, COALESCE(designation,'') AS designation, COALESCE(allowed_credits,'') AS allowed FROM faculty ORDER BY name")
    if fdf.empty:
        st.info("No faculty found. Add faculty first.")
        render_footer(); return

    name_list = fdf["name"].tolist()
    pick = st.selectbox("Select faculty", name_list, index=0)
    row = fdf[fdf["name"]==pick].iloc[0]
    fid = int(row["id"])
    designation = str(row["designation"] or "")
    allowed_override = int(row["allowed"]) if str(row["allowed"]).isdigit() else None

    # Compute credit target
    required = allowed_override if allowed_override is not None else _required_for_designation(designation)
    summary = _faculty_summary(fid)

    st.markdown(f"### {pick}")
    cols = st.columns(3)
    with cols[0]:
        st.metric("Designation", value=designation or "—")
        st.metric("Credit target", value=required)
    with cols[1]:
        st.metric("Subjects (In-Charge)", value=summary["subjects_incharge"])
        st.metric("Subjects (Faculty)", value=summary["subjects_faculty"])
    with cols[2]:
        st.metric("Total credits (assigned)", value=summary["credit_total"])

    if required and summary["credit_total"] < required:
        st.error("**Credits are deficient**")
    else:
        st.success("Credits meet or exceed the target.")

    st.markdown("#### Degree affiliations")
    if summary["degrees"]:
        st.write(", ".join(summary["degrees"]))
    else:
        st.caption("No degree affiliations recorded.")

    st.markdown("#### Class In-Charge assignments")
    if summary["class_incharge"]:
        st.table(pd.DataFrame(summary["class_incharge"]))
    else:
        st.caption("No class in-charge assignments.")

    # Optional: list subjects they are attached to (brief)
    with st.expander("Show subjects this faculty is attached to"):
        df_subj = read_df("""
            SELECT DISTINCT s.code, s.name, COALESCE(s.credits,0) AS credits,
                            CASE WHEN m.role='in_charge' THEN 'In-Charge' ELSE 'Faculty' END AS role
              FROM subject_faculty_map m
              JOIN subjects s ON s.id = m.subject_id
             WHERE m.faculty_id=?
             ORDER BY s.code, s.name
        """, (fid,))
        st.dataframe(df_subj if not df_subj.empty else pd.DataFrame({"info":["No subjects."]}), use_container_width=True)

    render_footer()
