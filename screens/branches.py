# screens/branches.py
from __future__ import annotations
import sqlite3
import pandas as pd
import streamlit as st
from core.db import get_conn, read_df, exec_sql, exec_many, ensure_base_schema

# ---------- helpers ----------
def _can_edit(role: str) -> bool:
    return (role or "").lower() in ("superadmin", "director", "principal")

def _int_or_none(x):
    try:
        if x is None: return None
        if isinstance(x, str):
            x = x.strip()
            if x == "": return None
        return int(float(x))
    except Exception:
        return None

def _faculty_name(fid: int) -> str:
    df = read_df("SELECT name FROM faculty WHERE id=?", (int(fid),))
    return (df["name"].iloc[0] if not df.empty else "—")

# ---------- screen ----------
def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    ensure_base_schema()
    st.header("Branches & Class In-Charge")

    editable = _can_edit(user.get("role",""))
    username = user.get("username","")

    # Degrees
    deg = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    if deg.empty:
        st.info("Add a Degree first.")
        return

    cdeg1, cdeg2 = st.columns([2,1])
    with cdeg1:
        pick_deg = st.selectbox("Degree / Program", deg["name"].tolist(), index=0, key="br_deg")
    with cdeg2:
        dur = int(deg[deg["name"] == pick_deg]["duration_years"].iloc[0])
        st.caption(f"Duration: **{dur} years**")

    degree_id = int(deg[deg["name"] == pick_deg]["id"].iloc[0])

    # Academic year (start) for CIC assignment
    batches = read_df(
        "SELECT DISTINCT CAST(SUBSTR(roll,1,4) AS INT) AS byear "
        "FROM students WHERE degree_id=? AND LENGTH(roll)>=4 ORDER BY 1 DESC",
        (degree_id,)
    )
    batch_options = [int(x) for x in batches["byear"].tolist()] if not batches.empty else []
    default_ay = batch_options[0] if batch_options else 2025
    ay_start = st.number_input("Academic Year (start, e.g., 2025 for 2025–26)",
                               min_value=1900, max_value=2100, value=default_ay, step=1, key="cic_ay")

    # >>> NEW: Show AY chosen right under the selector
    st.markdown(f"**Academic Year chosen:** **{int(ay_start)}–{int(ay_start)+1}**")

    st.divider()
    st.subheader("Add Branch")

    # Optional years via text boxes (avoid min/max issues)
    with st.form("add_branch", clear_on_submit=True):
        name = st.text_input("Branch name", placeholder="e.g., Humanities")
        coly1, coly2 = st.columns(2)
        with coly1:
            sy_txt = st.text_input("Start year (optional)", value="")
        with coly2:
            ey_txt = st.text_input("End year (optional)", value="")
        ok = st.form_submit_button("Add", disabled=not editable)

    if ok:
        if not name.strip():
            st.error("Branch name is required.")
        else:
            try:
                sy = _int_or_none(sy_txt)
                ey = _int_or_none(ey_txt)
                exec_sql(
                    "INSERT INTO branches(degree_id, name, start_year, end_year) VALUES(?,?,?,?)",
                    (degree_id, name.strip(), sy, ey)
                )
                st.success("Branch added.")
                st.rerun()
            except sqlite3.IntegrityError:
                # Legacy DB where branches.name was globally unique; disambiguate
                alt = f"{name.strip()} ({pick_deg})"
                try:
                    exec_sql(
                        "INSERT INTO branches(degree_id, name, start_year, end_year) VALUES(?,?,?,?)",
                        (degree_id, alt, sy, ey)
                    )
                    st.info(f"Legacy constraint detected. Saved as **{alt}**.")
                    st.rerun()
                except Exception as e2:
                    st.error(f"Add failed: {e2}")
            except Exception as e:
                st.error(f"Add failed: {e}")

    st.divider()
    st.subheader("Manage Branches & Branch Head")

    dfb = read_df("SELECT id, name, start_year, end_year FROM branches WHERE degree_id=? ORDER BY name", (degree_id,))
    if dfb.empty:
        st.info("No branches yet for this degree.")
    else:
        names = dfb["name"].tolist()
        sel = st.selectbox("Select branch", names, index=0, key="br_pick")
        row = dfb[dfb["name"] == sel].iloc[0]
        branch_id = int(row["id"])

        c1, c2, c3 = st.columns([2,1,1])
        with c1:
            new_name = st.text_input("Rename to", sel, key="br_rename_to")
        with c2:
            new_sy_txt = st.text_input("Start year (optional)", value="" if pd.isna(row["start_year"]) else str(int(row["start_year"])))
        with c3:
            new_ey_txt = st.text_input("End year (optional)", value="" if pd.isna(row["end_year"]) else str(int(row["end_year"])) )

        if st.button("Save Changes", disabled=not editable, key="br_save"):
            try:
                new_sy = _int_or_none(new_sy_txt)
                new_ey = _int_or_none(new_ey_txt)
                exec_sql("UPDATE branches SET name=?, start_year=?, end_year=? WHERE id=?",
                         (new_name.strip(), new_sy, new_ey, branch_id))
                st.success("Saved.")
                st.rerun()
            except sqlite3.IntegrityError:
                alt = f"{new_name.strip()} ({pick_deg})"
                try:
                    exec_sql("UPDATE branches SET name=?, start_year=?, end_year=? WHERE id=?",
                             (alt, new_sy, new_ey, branch_id))
                    st.info(f"Legacy constraint detected. Renamed to **{alt}**.")
                    st.rerun()
                except Exception as e2:
                    st.error(f"Update failed: {e2}")
            except Exception as e:
                st.error(f"Update failed: {e}")

        st.markdown("#### Branch Head (core faculty only)")
        fdf = read_df("SELECT id, name FROM faculty WHERE LOWER(COALESCE(type,''))='core' ORDER BY name")
        options = ["— None —"] + (fdf["name"].tolist() if not fdf.empty else [])
        cur = read_df("SELECT faculty_id FROM faculty_roles WHERE role_name='branch_head' AND slot=?", (branch_id,))
        current = "— None —"
        if not cur.empty:
            fid = int(cur.iloc[0]["faculty_id"])
            nm = read_df("SELECT name FROM faculty WHERE id=?", (fid,))
            if not nm.empty:
                current = str(nm.iloc[0]["name"])
        pick = st.selectbox("Select core faculty", options, index=(options.index(current) if current in options else 0), key="br_head_pick")

        if st.button("Set as Branch Head", disabled=not editable, key="br_set_head"):
            try:
                with get_conn() as conn:
                    c = conn.cursor()
                    c.execute("DELETE FROM faculty_roles WHERE role_name='branch_head' AND slot=?", (branch_id,))
                    if pick != "— None —":
                        fid = int(fdf[fdf["name"] == pick]["id"].iloc[0])
                        c.execute("INSERT INTO faculty_roles(role_name, faculty_id, slot, slot2, ay_start) VALUES('branch_head', ?, ?, NULL, NULL)", (fid, branch_id))
                    conn.commit()
                st.success("Updated branch head.")
            except Exception as e:
                st.error(f"Set head failed: {e}")

    st.divider()
    st.subheader("Class In-Charge per Degree & Academic Year")
    st.caption("A faculty can be **Class In-Charge only once per academic year** (across all degrees).")

    core_fac = read_df("SELECT id, name FROM faculty WHERE LOWER(COALESCE(type,''))='core' ORDER BY name")
    ci_options = ["— None —"] + (core_fac["name"].tolist() if not core_fac.empty else [])

    # current CIC map for this degree & ay_start
    current_map = read_df("""
        SELECT slot AS year, faculty_id
          FROM faculty_roles
         WHERE role_name='class_incharge' AND slot2=? AND ay_start=?
         ORDER BY slot
    """, (degree_id, int(ay_start)))
    cur_by_year = {int(r["year"]): int(r["faculty_id"]) for _, r in current_map.iterrows()} if not current_map.empty else {}

    picks = {}
    cols = st.columns(4)
    for y in range(1, int(dur) + 1):
        idx = (y - 1) % 4
        with cols[idx]:
            cur_name = "— None —"
            if y in cur_by_year:
                nm = read_df("SELECT name FROM faculty WHERE id=?", (cur_by_year[y],))
                if not nm.empty: cur_name = str(nm.iloc[0]["name"])
            picks[y] = st.selectbox(f"Year {y}", ci_options, index=(ci_options.index(cur_name) if cur_name in ci_options else 0), key=f"cic_{y}")

    if st.button("Save Class In-Charge", disabled=not editable, key="save_cic"):
        try:
            with get_conn() as conn:
                c = conn.cursor()

                changes = []  # (year, from_fid, to_fid)
                for y, nm in picks.items():
                    cur_fid = cur_by_year.get(y)
                    to_fid = None if nm == "— None —" else int(core_fac[core_fac["name"] == nm]["id"].iloc[0])

                    if (cur_fid or None) == to_fid:
                        continue

                    # global AY uniqueness for CIC
                    if to_fid is not None:
                        exists = c.execute("""
                            SELECT 1 FROM faculty_roles
                             WHERE role_name='class_incharge' AND faculty_id=? AND ay_start=? LIMIT 1
                        """, (to_fid, int(ay_start))).fetchone()
                        if exists:
                            st.warning(f"{_faculty_name(to_fid)} is already CIC in AY {int(ay_start)} — skipping Year {y}.")
                            continue

                    if to_fid is None:
                        c.execute("""
                            DELETE FROM faculty_roles
                             WHERE role_name='class_incharge' AND slot=? AND slot2=? AND ay_start=?
                        """, (int(y), degree_id, int(ay_start)))
                    else:
                        c.execute("""
                            INSERT INTO faculty_roles(role_name, faculty_id, slot, slot2, ay_start)
                            VALUES('class_incharge', ?, ?, ?, ?)
                            ON CONFLICT(role_name, slot, slot2, ay_start)
                            DO UPDATE SET faculty_id=excluded.faculty_id
                        """, (to_fid, int(y), degree_id, int(ay_start)))

                    changes.append((y, cur_fid, to_fid))

                for y, frm, to in changes:
                    c.execute("""
                        INSERT INTO cic_change_log(changed_by, degree_id, ay_start, year, from_faculty_id, to_faculty_id)
                        VALUES(?,?,?,?,?,?)
                    """, (username, degree_id, int(ay_start), int(y), (int(frm) if frm else None), (int(to) if to else None)))

                conn.commit()

            if changes:
                st.success("Class In-Charge updated.")
                st.rerun()
            else:
                st.info("No changes to save.")
        except sqlite3.IntegrityError:
            st.error("Save failed due to rule violation: a faculty cannot be CIC for more than one year in the same AY.")
        except Exception as e:
            st.error(f"Save failed: {e}")

    # >>> NEW: AY callout at the bottom of CIC section
    st.info(f"Academic Year selected for CIC: **{int(ay_start)}–{int(ay_start)+1}**")

    # -------- Change log --------
    st.divider()
    st.subheader("Class In-Charge change log")
    log = read_df("""
        SELECT l.changed_at,
               d.name AS degree,
               l.ay_start,
               l.year,
               COALESCE(f1.name, '—') AS from_name,
               COALESCE(f2.name, '—') AS to_name,
               COALESCE(l.changed_by, '—') AS changed_by
          FROM cic_change_log l
          JOIN degrees d ON d.id=l.degree_id
          LEFT JOIN faculty f1 ON f1.id=l.from_faculty_id
          LEFT JOIN faculty f2 ON f2.id=l.to_faculty_id
         WHERE l.degree_id=? AND l.ay_start=?
         ORDER BY l.changed_at DESC, l.year
    """, (degree_id, int(ay_start)))
    if log.empty:
        st.caption("No changes recorded yet for this Degree & AY.")
    else:
        st.dataframe(log.rename(columns={
            "changed_at":"Time",
            "degree":"Degree",
            "ay_start":"AY Start",
            "year":"Year",
            "from_name":"From",
            "to_name":"To",
            "changed_by":"Changed By"
        }), use_container_width=True)

    # -------- Export / Import --------
    st.divider()
    st.subheader("Export / Import Branches")

    exp = read_df("""
        SELECT d.name AS Degree, b.name AS Branch, b.start_year, b.end_year
          FROM branches b
          JOIN degrees d ON d.id=b.degree_id
         WHERE b.degree_id=?
         ORDER BY b.name
    """, (degree_id,))
    st.download_button(
        "Export branches (CSV)",
        data=(exp if not exp.empty else pd.DataFrame(columns=["Degree","Branch","start_year","end_year"]))
             .to_csv(index=False).encode("utf-8"),
        file_name=f"branches_{pick_deg}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    up = st.file_uploader("Import branches CSV (Degree, Branch, start_year, end_year)", type=["csv"], key="br_up")
    if up is not None and editable:
        try:
            df = pd.read_csv(up)
            need_cols = {"Degree","Branch"}
            if not need_cols.issubset(set(df.columns)):
                st.error("CSV must have at least columns: Degree, Branch")
            else:
                for _, r in df.iterrows():
                    nm = str(r.get("Branch","")).strip()
                    if not nm: continue
                    sy = _int_or_none(r.get("start_year"))
                    ey = _int_or_none(r.get("end_year"))
                    try:
                        exec_sql("INSERT INTO branches(degree_id, name, start_year, end_year) VALUES(?,?,?,?)",
                                 (degree_id, nm, sy, ey))
                    except sqlite3.IntegrityError:
                        alt = f"{nm} ({pick_deg})"
                        try:
                            exec_sql("INSERT INTO branches(degree_id, name, start_year, end_year) VALUES(?,?,?,?)",
                                     (degree_id, alt, sy, ey))
                        except Exception:
                            pass
                st.success("Import completed.")
                st.rerun()
        except Exception as e:
            st.error(f"Import failed: {e}")
