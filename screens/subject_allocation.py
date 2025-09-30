from __future__ import annotations

from typing import List, Optional, Tuple
import pandas as pd
import streamlit as st
import sqlite3

from core.db import (
    read_df,
    exec_sql,
    exec_many,
    get_conn,
    ensure_base_schema,
)

ALLOWED_EDIT_ROLES = {"superadmin", "director", "principal"}


# =========================
# Schema helpers & migration
# =========================

def _table_columns(table: str) -> List[str]:
    try:
        with get_conn() as conn:
            cur = conn.execute(f"PRAGMA table_info({table})")
            return [r[1] for r in cur.fetchall()]
    except Exception:
        return []

def _ensure_subject_offerings_columns():
    """
    Make sure subject_offerings has the columns we rely on.
    Adds columns if missing (safe no-op if they already exist).
    """
    cols = _table_columns("subject_offerings")
    if not cols:
        # Table not present at all – let ensure_base_schema create it.
        ensure_base_schema()
        cols = _table_columns("subject_offerings")

    to_add = []
    if "degree_id" not in cols:              to_add.append(("degree_id", "INTEGER"))
    if "batch_year" not in cols:             to_add.append(("batch_year", "INTEGER"))
    if "semester" not in cols:               to_add.append(("semester", "INTEGER"))
    if "branch_id" not in cols:              to_add.append(("branch_id", "INTEGER"))
    if "subject_id" not in cols:             to_add.append(("subject_id", "INTEGER"))
    if "topic_id" not in cols:               to_add.append(("topic_id", "INTEGER"))
    if "subject_in_charge_id" not in cols:   to_add.append(("subject_in_charge_id", "INTEGER"))
    if "updated_at" not in cols:             to_add.append(("updated_at", "TEXT"))

    if to_add:
        with get_conn() as conn:
            c = conn.cursor()
            for name, typ in to_add:
                c.execute(f"ALTER TABLE subject_offerings ADD COLUMN {name} {typ}")
            conn.commit()

    # Ensure subject_offering_faculty exists
    cols2 = _table_columns("subject_offering_faculty")
    if not cols2:
        ensure_base_schema()
        cols2 = _table_columns("subject_offering_faculty")
    need2 = []
    if "offering_id" not in cols2: need2.append(("offering_id", "INTEGER"))
    if "faculty_id" not in cols2:  need2.append(("faculty_id", "INTEGER"))
    if "role" not in cols2:        need2.append(("role", "TEXT"))
    if need2:
        with get_conn() as conn:
            c = conn.cursor()
            for name, typ in need2:
                c.execute(f"ALTER TABLE subject_offering_faculty ADD COLUMN {name} {typ}")
            conn.commit()


# ==============
# Small helpers
# ==============

def _can_edit(role: str) -> bool:
    return (role or "").lower() in ALLOWED_EDIT_ROLES

def _abs_sems_for_year(year: int) -> Tuple[int, int]:
    y = max(1, int(year or 1))
    s1 = 2 * (y - 1) + 1
    return s1, s1 + 1

def _compute_ay_label(ay_start: int) -> str:
    try:
        s = int(ay_start)
    except Exception:
        return "—"
    return f"{s}–{s+1}"

def _degree_picker() -> Optional[pd.Series]:
    df = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    if df.empty:
        st.info("Add a Degree in the **Degrees** page first.")
        return None
    pick = st.selectbox("Degree / Program", df["name"].tolist(), index=0, key="sa_degree")
    return df[df["name"] == pick].iloc[0]

def _batch_picker(degree_id: int) -> int:
    df = read_df("""
        SELECT DISTINCT CAST(SUBSTR(roll,1,4) AS INT) AS byear
          FROM students
         WHERE degree_id=? AND LENGTH(roll)>=4
         ORDER BY 1 DESC
    """, (int(degree_id),))
    opts = [int(x) for x in df["byear"].tolist()] if not df.empty else []
    default = opts[0] if opts else 2025
    val = st.number_input(
        "Batch / AY start (e.g., 2025 ⇢ 2025–26)",
        min_value=1900, max_value=2100, value=int(default), step=1, key="sa_ay"
    )
    st.caption(f"Academic Year chosen: **{_compute_ay_label(int(val))}**")
    return int(val)

def _year_sem_picker(dur_years: int) -> Tuple[int, int]:
    years = [f"Year {i}" for i in range(1, int(dur_years) + 1)]
    pick = st.selectbox("Year", years, index=0, key="sa_year")
    y = int(pick.split()[-1])
    s1, s2 = _abs_sems_for_year(y)
    sem = st.selectbox("Semester", [s1, s2], index=0, key="sa_sem")
    return y, int(sem)

def _branches_for_degree(degree_id: int) -> pd.DataFrame:
    df = read_df("SELECT id, name, branch_head_faculty_id FROM branches WHERE degree_id=? ORDER BY name", (int(degree_id),))
    if df.empty:
        return pd.DataFrame(columns=["id", "name", "branch_head_faculty_id"])
    return df

def _faculty_lists() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    core = read_df("SELECT id, name FROM faculty WHERE LOWER(COALESCE(type,''))='core' ORDER BY name")
    visit= read_df("SELECT id, name FROM faculty WHERE LOWER(COALESCE(type,''))='visiting' ORDER BY name")
    allf = pd.concat([core, visit], ignore_index=True) if not core.empty or not visit.empty else pd.DataFrame(columns=["id","name"])
    return core, visit, allf

def _fac_names(all_fac: pd.DataFrame, ids: List[int]) -> List[str]:
    out = []
    if all_fac.empty: return out
    for fid in ids:
        r = all_fac[all_fac["id"] == int(fid)]
        if not r.empty:
            out.append(str(r["name"].iloc[0]))
    return out

def _ids_from_names(all_fac: pd.DataFrame, names: List[str]) -> List[int]:
    out = []
    for n in names:
        r = all_fac[all_fac["name"] == n]
        if not r.empty:
            out.append(int(r["id"].iloc[0]))
    return out

def _class_incharge_label(degree_id: int, ay_start: int, year: int) -> str:
    df = read_df("""
        SELECT b.name AS branch,
               f.name AS cic
          FROM branches b
          LEFT JOIN faculty f ON f.id=b.class_incharge_faculty_id
         WHERE b.degree_id=? AND IFNULL(b.ay_start, ?) = ? AND IFNULL(b.year, ?) = ?
         ORDER BY b.name
    """, (degree_id, ay_start, ay_start, year, year))
    if df.empty:
        return "—"
    vals = []
    for _, r in df.iterrows():
        vals.append(f"{r['branch']}: {r['cic'] if pd.notna(r['cic']) else '—'}")
    return "; ".join(vals)


# =========================
# Subject & Topic fetchers
# =========================

def _subjects_for_sem(degree_id:int, sem:int, branch_id: Optional[int]) -> pd.DataFrame:
    sql = """
        SELECT DISTINCT
            sc.id AS subject_id,
            COALESCE(sc.code, s.code) AS subject_code,
            COALESCE(s.name, sc.code) AS subject_name
        FROM subject_criteria sc
        LEFT JOIN subjects s
               ON s.degree_id=sc.degree_id
              AND s.semester=sc.semester
              AND LOWER(COALESCE(s.code,''))=LOWER(COALESCE(sc.code,''))
       WHERE sc.degree_id=? AND sc.semester=?
    """
    params = [int(degree_id), int(sem)]
    if branch_id is not None:
        sql += " AND (sc.branch_id IS NULL OR sc.branch_id=?)"
        params.append(int(branch_id))
    sql += " ORDER BY subject_code, subject_name"
    df = read_df(sql, tuple(params))
    if not df.empty:
        return df

    # fallback to subjects
    df2 = read_df("""
        SELECT id AS subject_id, code AS subject_code, name AS subject_name
          FROM subjects
         WHERE degree_id=? AND semester=?
         ORDER BY code, name
    """, (int(degree_id), int(sem)))
    return df2

def _topics_for_subject(subject_id: int) -> pd.DataFrame:
    df = read_df("""
        SELECT id AS topic_id, topic_code, COALESCE(title,'') AS title
          FROM subject_topics
         WHERE subject_id=?
         ORDER BY topic_code, title
    """, (int(subject_id),))
    if df.empty:
        return pd.DataFrame(columns=["topic_id","topic_code","title"])
    return df


# ==============================
# Allocation helpers (NEW schema)
# ==============================

def _ensure_offering(degree_id:int, batch_year:int, sem:int,
                     branch_id: Optional[int], subject_id:int, topic_id: Optional[int]) -> int:
    _ensure_subject_offerings_columns()
    row = read_df("""
        SELECT id FROM subject_offerings
         WHERE degree_id=? AND batch_year=? AND semester=?
           AND IFNULL(branch_id,-1)=IFNULL(?, -1)
           AND subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
         LIMIT 1
    """, (int(degree_id), int(batch_year), int(sem),
          branch_id, int(subject_id), topic_id))
    if not row.empty:
        return int(row["id"].iloc[0])

    exec_sql("""
        INSERT INTO subject_offerings(degree_id, batch_year, semester, branch_id, subject_id, topic_id)
        VALUES(?,?,?,?,?,?)
    """, (int(degree_id), int(batch_year), int(sem),
          branch_id, int(subject_id), topic_id))

    row2 = read_df("""
        SELECT id FROM subject_offerings
         WHERE degree_id=? AND batch_year=? AND semester=?
           AND IFNULL(branch_id,-1)=IFNULL(?, -1)
           AND subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
         ORDER BY id DESC LIMIT 1
    """, (int(degree_id), int(batch_year), int(sem),
          branch_id, int(subject_id), topic_id))
    return int(row2["id"].iloc[0])

def _load_offering_members(offering_id:int) -> Tuple[Optional[int], List[int], List[int]]:
    a = read_df("SELECT subject_in_charge_id FROM subject_offerings WHERE id=?", (int(offering_id),))
    sic = int(a["subject_in_charge_id"].iloc[0]) if (not a.empty and pd.notna(a["subject_in_charge_id"].iloc[0])) else None
    lect = read_df(
        "SELECT faculty_id FROM subject_offering_faculty WHERE offering_id=? AND role='lecture' ORDER BY faculty_id",
        (int(offering_id),)
    )
    stud = read_df(
        "SELECT faculty_id FROM subject_offering_faculty WHERE offering_id=? AND role='studio'  ORDER BY faculty_id",
        (int(offering_id),)
    )
    lect_ids = [int(x) for x in (lect["faculty_id"].tolist() if not lect.empty else [])]
    stud_ids = [int(x) for x in (stud["faculty_id"].tolist() if not stud.empty else [])]
    return sic, lect_ids, stud_ids

def _save_offering(offering_id:int, sic_id: Optional[int], lect_ids: List[int], stud_ids: List[int]) -> None:
    total = (1 if sic_id else 0) + len(set(lect_ids)) + len(set(stud_ids))
    if total > 10:
        raise ValueError("Maximum 10 faculty allowed in total (including Subject In-Charge).")

    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE subject_offerings SET subject_in_charge_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (sic_id, int(offering_id))
        )
        c.execute("DELETE FROM subject_offering_faculty WHERE offering_id=?", (int(offering_id),))
        for fid in sorted(set(lect_ids)):
            c.execute(
                "INSERT OR IGNORE INTO subject_offering_faculty(offering_id, faculty_id, role) VALUES(?,?, 'lecture')",
                (int(offering_id), int(fid))
            )
        for fid in sorted(set(stud_ids)):
            c.execute(
                "INSERT OR IGNORE INTO subject_offering_faculty(offering_id, faculty_id, role) VALUES(?,?, 'studio')",
                (int(offering_id), int(fid))
            )
        conn.commit()


# =========================
# Export / Import / Clone
# =========================

def _export_grid(degree_id:int, batch_year:int, sem:int, branch_id: Optional[int]) -> bytes:
    _ensure_subject_offerings_columns()
    df = read_df("""
        SELECT so.id AS alloc_id,
               COALESCE(sc.code, s.code) AS subject_code,
               t.topic_code,
               b.name AS branch_name,
               so.subject_in_charge_id AS sic_faculty_id,
               GROUP_CONCAT(CASE WHEN sof.role='lecture' THEN sof.faculty_id END) AS lecture_faculty_ids,
               GROUP_CONCAT(CASE WHEN sof.role='studio'  THEN sof.faculty_id END) AS studio_faculty_ids
          FROM subject_offerings so
          LEFT JOIN subject_criteria sc ON sc.id=so.subject_id
          LEFT JOIN subjects s
                 ON s.degree_id=sc.degree_id    -- uses sc to be robust
                AND s.semester=sc.semester
                AND LOWER(COALESCE(s.code,''))=LOWER(COALESCE(sc.code,''))
          LEFT JOIN subject_topics t ON t.id=so.topic_id
          LEFT JOIN branches b ON b.id=so.branch_id
          LEFT JOIN subject_offering_faculty sof ON sof.offering_id=so.id
         WHERE sc.degree_id=? AND so.batch_year=? AND sc.semester=? AND IFNULL(so.branch_id,-1)=IFNULL(?, -1)
         GROUP BY so.id, subject_code, t.topic_code, branch_name, so.subject_in_charge_id
         ORDER BY subject_code, t.topic_code
    """, (int(degree_id), int(batch_year), int(sem), branch_id))
    return df.to_csv(index=False).encode("utf-8")

def _import_grid(degree_id:int, batch_year:int, sem:int, branch_id_page: Optional[int], fp) -> Tuple[int,int]:
    _ensure_subject_offerings_columns()
    df = pd.read_csv(fp)
    ok = 0; skip = 0

    subs = read_df("""
        SELECT sc.id AS subject_id, COALESCE(sc.code, s.code) AS subject_code
          FROM subject_criteria sc
          LEFT JOIN subjects s
                 ON s.degree_id=sc.degree_id
                AND s.semester=sc.semester
                AND LOWER(COALESCE(s.code,''))=LOWER(COALESCE(sc.code,''))
         WHERE sc.degree_id=? AND sc.semester=?
    """, (int(degree_id), int(sem)))
    code2sid = {str(r["subject_code"]).lower(): int(r["subject_id"]) for _, r in subs.iterrows()}

    br = read_df("SELECT id, name FROM branches WHERE degree_id=? ORDER BY name", (int(degree_id),))
    bname2bid = {str(r["name"]).strip().lower(): int(r["id"]) for _, r in br.iterrows()}

    topics = read_df("""
        SELECT t.id AS topic_id, t.topic_code, sc.id AS subject_id
          FROM subject_topics t
          JOIN subject_criteria sc ON sc.id=t.subject_id
         WHERE sc.degree_id=? AND sc.semester=?
    """, (int(degree_id), int(sem)))
    key2tid = {(int(r["subject_id"]), str(r["topic_code"]).lower()): int(r["topic_id"]) for _, r in topics.iterrows()}

    def _parse_ids(s: str) -> List[int]:
        if pd.isna(s) or str(s).strip()=="":
            return []
        out=[]
        for tok in str(s).split(","):
            tok = tok.strip()
            if not tok: continue
            try: out.append(int(tok))
            except: pass
        return out

    for _, r in df.iterrows():
        scode = str(r.get("subject_code","")).strip().lower()
        if not scode or scode not in code2sid:
            skip += 1; continue
        subject_id = code2sid[scode]

        tcode = str(r.get("topic_code","")).strip().lower()
        topic_id = None
        if tcode:
            topic_id = key2tid.get((subject_id, tcode))
            if topic_id is None:
                skip += 1; continue

        bname = r.get("branch_name")
        if pd.isna(bname) or str(bname).strip()=="":
            branch_id = branch_id_page
        else:
            branch_id = bname2bid.get(str(bname).strip().lower())

        sic_id = None
        try:
            if pd.notna(r.get("sic_faculty_id")):
                sic_id = int(r.get("sic_faculty_id"))
        except Exception:
            sic_id = None

        lect_ids = _parse_ids(r.get("lecture_faculty_ids"))
        stud_ids = _parse_ids(r.get("studio_faculty_ids"))

        try:
            offering_id = _ensure_offering(int(degree_id), int(batch_year), int(sem), branch_id, int(subject_id), topic_id)
            _save_offering(offering_id, sic_id, lect_ids, stud_ids)
            ok += 1
        except Exception:
            skip += 1

    return ok, skip


# === UI ===

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    # make sure schema is ready and columns exist
    ensure_base_schema()
    _ensure_subject_offerings_columns()

    st.header("Faculty → Subject Allocation")
    can_edit = _can_edit(user.get("role",""))

    drow = _degree_picker()
    if drow is None: return
    degree_id = int(drow["id"])
    dur_years = int(drow["duration_years"])

    ay_start = _batch_picker(degree_id)
    year, sem = _year_sem_picker(dur_years)

    branches = _branches_for_degree(degree_id)
    br_options = ["— All / Not branch-specific —"] + (branches["name"].tolist() if not branches.empty else [])
    br_pick = st.selectbox("Filter by Branch (optional)", br_options, index=0, key="sa_page_branch")
    branch_filter_id = None if br_pick == "— All / Not branch-specific —" else int(branches[branches["name"]==br_pick]["id"].iloc[0])

    st.caption(f"**Academic Year:** {_compute_ay_label(ay_start)}  |  **Class In-Charge(s):** {_class_incharge_label(degree_id, ay_start, year)}")
    st.divider()

    subs = _subjects_for_sem(degree_id, sem, branch_filter_id)
    if subs.empty:
        st.info("No subjects found for the chosen Degree + Semester.")
        return

    core, visit, allfac = _faculty_lists()

    rows = []
    for _, srow in subs.iterrows():
        sid   = int(srow["subject_id"])
        scode = str(srow["subject_code"])
        sname = str(srow["subject_name"])
        rows.append({"subject_id": sid, "subject_code": scode, "subject_name": sname, "topic_id": None, "topic_code": "", "title": ""})
        tops = _topics_for_subject(sid)
        for _, t in tops.iterrows():
            rows.append({"subject_id": sid, "subject_code": scode, "subject_name": sname,
                         "topic_id": int(t["topic_id"]), "topic_code": str(t["topic_code"] or ""), "title": str(t["title"] or "")})

    st.markdown("### Allocation Grid")
    st.caption("Tip: Set **Branch** (optional), then **SIC** (core only), then **Lecture/Studio**. Max 10 people (including SIC) per row.")

    c1,c2,c3 = st.columns([1,1,1])
    with c1:
        data = _export_grid(degree_id, ay_start, sem, branch_filter_id)
        st.download_button("Export CSV", data=data, file_name=f"subject_alloc_{degree_id}_{ay_start}_S{sem}.csv",
                           mime="text/csv", use_container_width=True)
    with c2:
        up = st.file_uploader("Import CSV", type=["csv"], key="sa_import")
        if up is not None and can_edit:
            try:
                ok, skip = _import_grid(degree_id, ay_start, sem, branch_filter_id, up)
                st.success(f"Import done: {ok} updated, {skip} skipped.")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")
    with c3:
        with st.form(key="sa_clone_form", border=True):
            clone_from = st.number_input("Clone from batch (AY start)", 1900, 2100, value=max(1900, ay_start-1), step=1, key="sa_clone_from")
            submitted = st.form_submit_button("Clone", disabled=not can_edit, use_container_width=True)
            if submitted:
                try:
                    src = read_df("""
                        SELECT id, subject_id, topic_id, branch_id, subject_in_charge_id
                          FROM subject_offerings
                         WHERE degree_id=? AND batch_year=? AND semester=? AND IFNULL(branch_id,-1)=IFNULL(?, -1)
                    """, (degree_id, int(clone_from), sem, branch_filter_id))
                    if src.empty:
                        st.warning("Nothing to clone for the chosen source batch.")
                    else:
                        with get_conn() as conn:
                            c = conn.cursor()
                            for _, r in src.iterrows():
                                sid = int(r["subject_id"])
                                tid = int(r["topic_id"]) if pd.notna(r["topic_id"]) else None
                                bid = int(r["branch_id"]) if pd.notna(r["branch_id"]) else None
                                off_now = _ensure_offering(degree_id, ay_start, sem, bid, sid, tid)
                                c.execute(
                                    "UPDATE subject_offerings SET subject_in_charge_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                    (int(r["subject_in_charge_id"]) if pd.notna(r["subject_in_charge_id"]) else None, off_now)
                                )
                                c.execute("DELETE FROM subject_offering_faculty WHERE offering_id=?", (off_now,))
                                mem = read_df("SELECT faculty_id, role FROM subject_offering_faculty WHERE offering_id=?", (int(r["id"]),))
                                for _, m in mem.iterrows():
                                    c.execute(
                                        "INSERT OR IGNORE INTO subject_offering_faculty(offering_id, faculty_id, role) VALUES(?,?,?)",
                                        (int(off_now), int(m["faculty_id"]), str(m["role"]))
                                    )
                            conn.commit()
                        st.success("Cloned allocations.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Clone failed: {e}")

    st.divider()

    branch_names = branches["name"].tolist() if not branches.empty else []
    fid2name = {}
    if not (core.empty and visit.empty):
        fac = pd.concat([core, visit], ignore_index=True) if not core.empty or not visit.empty else pd.DataFrame(columns=["id","name"])
        for _, r in fac.iterrows(): fid2name[int(r["id"])] = str(r["name"])

    idx = 0
    for r in rows:
        idx += 1
        sid = int(r["subject_id"])
        tid = int(r["topic_id"]) if r["topic_id"] not in (None, "", pd.NA) else None
        scode = r["subject_code"]; sname = r["subject_name"]
        tcode = r["topic_code"];  ttitle= r["title"]

        header = f"{idx}. `{scode}` — {sname}"
        if tid:
            header += f"  ·  **Topic:** `{tcode}` {('— ' + ttitle) if ttitle else ''}"

        with st.expander(header, expanded=False):
            # pre-load current saved branch
            curr = read_df("""
                SELECT branch_id FROM subject_offerings
                 WHERE degree_id=? AND batch_year=? AND semester=?
                   AND subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
                 ORDER BY updated_at DESC, id DESC LIMIT 1
            """, (degree_id, ay_start, sem, sid, tid))
            br_default_index = 0
            br_label_list = ["— Not branch-specific —"] + branch_names
            current_branch_id = None
            if not curr.empty and pd.notna(curr["branch_id"].iloc[0]):
                bid = int(curr["branch_id"].iloc[0])
                if not branches.empty:
                    nm = branches[branches["id"]==bid]["name"]
                    if not nm.empty:
                        try:
                            br_default_index = br_label_list.index(str(nm.iloc[0]))
                            current_branch_id = bid
                        except Exception:
                            pass
            if current_branch_id is None and branch_filter_id is not None:
                nm = branches[branches["id"]==branch_filter_id]["name"]
                if not nm.empty:
                    try:
                        br_default_index = br_label_list.index(str(nm.iloc[0]))
                    except Exception:
                        pass
                current_branch_id = branch_filter_id

            br_pick = st.selectbox(
                "Branch (optional)",
                br_label_list,
                index=br_default_index,
                key=f"sa_row_branch_{degree_id}_{ay_start}_{sem}_{sid}_{tid}",
            )
            sel_branch_id = None
            if br_pick != "— Not branch-specific —":
                sel_branch_id = int(branches[branches["name"] == br_pick]["id"].iloc[0])

            bh_name = "—"
            if sel_branch_id is not None:
                bh = read_df("SELECT branch_head_faculty_id FROM branches WHERE id=?", (int(sel_branch_id),))
                if not bh.empty and pd.notna(bh["branch_head_faculty_id"].iloc[0]):
                    bh_name = fid2name.get(int(bh["branch_head_faculty_id"].iloc[0]), "—")
            st.write(f"**Branch Head:** {bh_name}")

            offering_id = _ensure_offering(degree_id, ay_start, sem, sel_branch_id, sid, tid)

            sic_cur, lect_ids_cur, stud_ids_cur = _load_offering_members(offering_id)
            if sic_cur is None:
                d0 = read_df("SELECT sic_faculty_id FROM subject_criteria WHERE id=?", (sid,))
                if not d0.empty and pd.notna(d0["sic_faculty_id"].iloc[0]):
                    sic_cur = int(d0["sic_faculty_id"].iloc[0])

            core_names = core["name"].tolist() if not core.empty else []
            sic_names = ["— None —"] + core_names
            default_sic_name = "— None —"
            if sic_cur is not None:
                nm = read_df("SELECT name FROM faculty WHERE id=?", (int(sic_cur),))
                if not nm.empty:
                    default_sic_name = nm["name"].iloc[0]

            sic_pick = st.selectbox(
                "Subject In-Charge (core only)",
                sic_names,
                index=(sic_names.index(default_sic_name) if default_sic_name in sic_names else 0),
                key=f"sa_row_sic_{degree_id}_{ay_start}_{sem}_{sid}_{tid}",
            )
            sic_id = None if sic_pick == "— None —" else int(core[core["name"]==sic_pick]["id"].iloc[0])

            all_names = (pd.concat([core, visit], ignore_index=True)["name"].tolist()
                         if (not core.empty or not visit.empty) else [])
            default_lect_names = _fac_names(pd.concat([core, visit], ignore_index=True) if (not core.empty or not visit.empty) else pd.DataFrame(columns=["id","name"]),
                                            list(set(lect_ids_cur + ([sic_id] if sic_id else []))))
            default_stud_names = _fac_names(pd.concat([core, visit], ignore_index=True) if (not core.empty or not visit.empty) else pd.DataFrame(columns=["id","name"]),
                                            list(set(stud_ids_cur + ([sic_id] if sic_id else []))))

            lect_sel = st.multiselect(
                "Lecture Faculty (include SIC if they also lecture)",
                all_names,
                default=default_lect_names,
                key=f"sa_row_lect_{degree_id}_{ay_start}_{sem}_{sid}_{tid}",
            )
            stud_sel = st.multiselect(
                "Studio Faculty (include SIC if they also take studio)",
                all_names,
                default=default_stud_names,
                key=f"sa_row_stud_{degree_id}_{ay_start}_{sem}_{sid}_{tid}",
            )

            allfac_df = pd.concat([core, visit], ignore_index=True) if (not core.empty or not visit.empty) else pd.DataFrame(columns=["id","name"])
            lect_ids = _ids_from_names(allfac_df, lect_sel)
            stud_ids = _ids_from_names(allfac_df, stud_sel)

            all_union = sorted(set(lect_sel) | set(stud_sel))
            st.write("**All Faculty (computed):** " + (", ".join(all_union) if all_union else "—"))

            if st.button("Save row", disabled=not can_edit, key=f"sa_row_save_{degree_id}_{ay_start}_{sem}_{sid}_{tid}"):
                try:
                    _save_offering(offering_id, sic_id, lect_ids, stud_ids)
                    st.success("Saved.")
                except Exception as e:
                    st.error(f"Save failed: {e}")

            st.markdown("---")
            st.markdown("**Topics (Electives / College Projects)**")
            cta1, cta2 = st.columns([1,1])
            with cta1:
                if st.button("Add topic", disabled=not can_edit, key=f"sa_add_topic_{sid}"):
                    tdf = _topics_for_subject(sid)
                    pref = 'e' if scode.lower().startswith('e') else ('p' if scode.lower().startswith('p') else 't')
                    base_num = ''.join([ch for ch in scode if ch.isdigit()]) or scode.lower()
                    max_n = 0
                    for _, rr in tdf.iterrows():
                        tc = str(rr["topic_code"] or "")
                        if '-' in tc:
                            try:
                                n = int(tc.split('-')[-1]); max_n = max(max_n, n)
                            except: pass
                    new_code = f"{pref}{base_num}-{max_n+1}"
                    try:
                        exec_sql("INSERT INTO subject_topics(subject_id, topic_code) VALUES(?,?)", (int(sid), new_code))
                        st.success(f"Topic added: {new_code}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Add topic failed: {e}")
            with cta2:
                tops_df = _topics_for_subject(sid)
                opts = ["— Select topic to delete —"] + [str(x) for x in tops_df["topic_code"].tolist()]
                which = st.selectbox("Delete topic (careful)", opts, index=0, key=f"sa_del_topic_pick_{sid}")
                if which != "— Select topic to delete —":
                    tid_del = int(tops_df[tops_df["topic_code"] == which]["topic_id"].iloc[0])
                    if st.button("Confirm delete topic", type="secondary", key=f"sa_del_topic_btn_{sid}"):
                        try:
                            with get_conn() as conn:
                                c = conn.cursor()
                                offs = read_df("SELECT id FROM subject_offerings WHERE subject_id=? AND topic_id=?", (int(sid), int(tid_del)))
                                for _, orow in offs.iterrows():
                                    c.execute("DELETE FROM subject_offering_faculty WHERE offering_id=?", (int(orow["id"]),))
                                c.execute("DELETE FROM subject_offerings WHERE subject_id=? AND topic_id=?", (int(sid), int(tid_del)))
                                c.execute("DELETE FROM subject_topics WHERE id=?", (int(tid_del),))
                                conn.commit()
                            st.success("Topic deleted.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Delete failed: {e}")

    st.divider()
    st.caption("All changes are saved row-wise. Use **Export** to backup, and **Import** to restore/apply in bulk.")
