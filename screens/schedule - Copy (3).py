# screens/schedule.py
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
import streamlit as st

# ---- DB imports with back-compat shim ----
try:
    from core.db import read_df, exec_one, exec_many
except ImportError:
    from core.db import read_df, execute as exec_one, exec_many  # type: ignore


# ========================== small utilities ==========================

def _safe_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

def _weekday_name(d: date) -> str:
    return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]

def _ay_for_year_of_batch(batch_year: int, year: int) -> int:
    """Academic-year start for a given (batch, year). Year 1 => batch_year, Year 4 => batch_year+3, etc."""
    return int(batch_year) + (int(year) - 1)

def _get_degrees() -> pd.DataFrame:
    try:
        return read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    except Exception:
        return pd.DataFrame(columns=["id","name","duration_years"])

def _abs_sems_for_year(year: int, max_sem: int) -> List[int]:
    y = int(year)
    s1, s2 = 2*(y-1)+1, 2*(y-1)+2
    return [s for s in (s1, s2) if s <= max_sem]

def _get_principal_name() -> str:
    try:
        df = read_df("""
            SELECT f.name
              FROM faculty_roles fr
              JOIN faculty f ON f.id=fr.faculty_id
             WHERE fr.role_name='principal'
             LIMIT 1
        """)
        if not df.empty: return str(df["name"].iloc[0])
    except Exception:
        pass
    return "—"

def _branch_df_for_degree(degree_id: int) -> pd.DataFrame:
    try:
        return read_df("SELECT id, name, COALESCE(branch_head_faculty_id, NULL) AS branch_head_faculty_id "
                       "FROM branches WHERE degree_id=? ORDER BY name", (int(degree_id),))
    except Exception:
        return pd.DataFrame(columns=["id","name","branch_head_faculty_id"])

def _branch_head_name(branch_id: Optional[int]) -> str:
    if not branch_id: return "—"
    try:
        df = read_df("""
            SELECT f.name
              FROM branches b
              JOIN faculty f ON f.id=b.branch_head_faculty_id
             WHERE b.id=? LIMIT 1
        """, (int(branch_id),))
        if not df.empty: return str(df["name"].iloc[0])
    except Exception:
        pass
    return "—"

def _class_incharge_name(degree_id: int, year: int, ay_start: int) -> str:
    try:
        df = read_df("""
            SELECT f.name
              FROM branches b
              JOIN faculty f ON f.id=b.class_incharge_faculty_id
             WHERE b.degree_id=? AND b.year=? AND b.ay_start=? LIMIT 1
        """, (int(degree_id), int(year), int(ay_start)))
        if not df.empty: return str(df["name"].iloc[0])
    except Exception:
        pass
    return "—"

def _subjects_for(degree_id: int, batch_year: int, abs_sem: int) -> pd.DataFrame:
    # Prefer subject_criteria rows for this context
    try:
        df = read_df("""
            SELECT id, code, name,
                   COALESCE(subject_in_charge_id, NULL) AS sic_id,
                   COALESCE(branch_id, NULL)             AS branch_id
              FROM subject_criteria
             WHERE degree_id=? AND batch_year=? AND semester=?
             ORDER BY code, name
        """, (int(degree_id), int(batch_year), int(abs_sem)))
        if not df.empty: return df
    except Exception:
        pass
    # fallback to subjects table by absolute semester (older data)
    try:
        df2 = read_df("SELECT id, code, name, NULL AS sic_id, NULL AS branch_id "
                      "FROM subjects WHERE semester=? ORDER BY code, name", (int(abs_sem),))
        return df2
    except Exception:
        return pd.DataFrame(columns=["id","code","name","sic_id","branch_id"])

def _subject_faculty(subject_id: int) -> Dict[str, Any]:
    out = dict(sic_id=None, sic_name="—", lec_ids=[], lec_names=[], stu_ids=[], stu_names=[])
    try:
        sc = read_df("SELECT COALESCE(subject_in_charge_id,NULL) AS sic_id FROM subject_criteria WHERE id=? LIMIT 1",
                     (int(subject_id),))
        if not sc.empty and pd.notna(sc["sic_id"].iloc[0]):
            out["sic_id"] = _safe_int(sc["sic_id"].iloc[0])
            nm = read_df("SELECT name FROM faculty WHERE id=? LIMIT 1", (int(out["sic_id"]),))
            if not nm.empty: out["sic_name"] = str(nm["name"].iloc[0])
    except Exception:
        pass
    try:
        fac = read_df("""
            SELECT m.kind, f.id, f.name
              FROM subject_faculty_map m
              JOIN faculty f ON f.id=m.faculty_id
             WHERE m.subject_id=? ORDER BY f.name
        """, (int(subject_id),))
        if not fac.empty:
            out["lec_ids"]   = fac[fac["kind"]=="lecture"]["id"].tolist()
            out["lec_names"] = fac[fac["kind"]=="lecture"]["name"].tolist()
            out["stu_ids"]   = fac[fac["kind"]=="studio"]["id"].tolist()
            out["stu_names"] = fac[fac["kind"]=="studio"]["name"].tolist()
    except Exception:
        pass
    return out

def _assignments_for_subject(subject_id: int) -> pd.DataFrame:
    try:
        return read_df("""
            SELECT id, COALESCE(title, code) AS title
              FROM assignments
             WHERE subject_id=? ORDER BY id
        """, (int(subject_id),))
    except Exception:
        return pd.DataFrame(columns=["id","title"])

def _holidays_between(start: date, end: date) -> List[date]:
    try:
        df = read_df("SELECT date FROM holidays WHERE date BETWEEN ? AND ? ORDER BY date",
                     (start.isoformat(), end.isoformat()))
        return [pd.to_datetime(x).date() for x in df["date"].tolist()] if not df.empty else []
    except Exception:
        return []

def _sessions_for(subject_id: int, start: date, end: date) -> pd.DataFrame:
    try:
        df = read_df("""
            SELECT id, session_date, slot, kind,
                   COALESCE(lectures,0) AS lectures,
                   COALESCE(studios,0)  AS studios,
                   COALESCE(lecture_notes,'') AS lecture_notes,
                   COALESCE(studio_notes,'')  AS studio_notes,
                   assignment_id, due_date,
                   COALESCE(completed,'') AS completed,
                   COALESCE(batch_year,NULL) AS batch_year,
                   COALESCE(semester,NULL)  AS semester,
                   COALESCE(branch_id,NULL) AS branch_id,
                   COALESCE(branch_head_faculty_id,NULL) AS branch_head_faculty_id
              FROM subject_sessions
             WHERE subject_id=? AND session_date BETWEEN ? AND ?
             ORDER BY session_date, id
        """, (int(subject_id), start.isoformat(), end.isoformat()))
        if df.empty:
            return pd.DataFrame(columns=[
                "id","session_date","slot","kind","lectures","studios",
                "lecture_notes","studio_notes","assignment_id","due_date","completed",
                "batch_year","semester","branch_id","branch_head_faculty_id"
            ])
        df["session_date"] = pd.to_datetime(df["session_date"]).dt.date
        if "due_date" in df and not df["due_date"].isna().all():
            df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce").dt.date
        return df
    except Exception:
        return pd.DataFrame(columns=[
            "id","session_date","slot","kind","lectures","studios",
            "lecture_notes","studio_notes","assignment_id","due_date","completed",
            "batch_year","semester","branch_id","branch_head_faculty_id"
        ])

def _notif(recipient_faculty_ids: Iterable[int], subject_id: int, message: str):
    """Try notifications(seen_by_faculty_id, subject_id, message), fallback to minimal."""
    rows = [(_safe_int(fid), int(subject_id), message) for fid in recipient_faculty_ids if _safe_int(fid)]
    if not rows: return
    try:
        exec_many("INSERT INTO notifications(seen_by_faculty_id, subject_id, message) VALUES(?,?,?)", rows)
    except Exception:
        try:
            exec_many("INSERT INTO notifications(subject_id, message) VALUES(?,?)",
                      [(int(subject_id), message)] * len(rows))
        except Exception:
            pass

def get_cic_name(degree_id:int, ay_start:int, year:int) -> str:
    df = read_df("""
        SELECT f.name
          FROM faculty_roles r
          JOIN faculty f ON f.id=r.faculty_id
         WHERE r.role_name='class_incharge'
           AND r.slot=?           -- year (1..N)
           AND r.slot2=?          -- degree_id
           AND r.ay_start=?       -- AY start
         LIMIT 1
    """, (int(year), int(degree_id), int(ay_start)))
    return df["name"].iloc[0] if not df.empty else "—"


def _table_has_cols(table: str, want_cols: Sequence[str]) -> Dict[str, bool]:
    """Return {col: True/False} for presence in PRAGMA table_info."""
    present = {c: False for c in want_cols}
    try:
        info = read_df(f"PRAGMA table_info({table})")
        have = set(info["name"].tolist()) if not info.empty else set()
        for c in want_cols: present[c] = c in have
    except Exception:
        pass
    return present

def _save_sessions_bulk(rows: List[Dict[str, Any]]):
    """Adaptive insert depending on existing columns of subject_sessions."""
    if not rows:
        return
    cols_ok = _table_has_cols("subject_sessions",
                              ["topic_id","batch_year","semester","branch_id","branch_head_faculty_id"])
    # construct column list & payload
    base_cols = [
        "subject_id","session_date","slot","kind","lectures","studios",
        "lecture_notes","studio_notes","assignment_id","due_date","completed"
    ]
    opt_cols = []
    if cols_ok.get("topic_id"):               opt_cols.append("topic_id")
    if cols_ok.get("batch_year"):             opt_cols.append("batch_year")
    if cols_ok.get("semester"):               opt_cols.append("semester")
    if cols_ok.get("branch_id"):              opt_cols.append("branch_id")
    if cols_ok.get("branch_head_faculty_id"): opt_cols.append("branch_head_faculty_id")

    all_cols = base_cols.copy()
    # put topic_id right after subject_id for readability
    if "topic_id" in opt_cols:
        all_cols = ["subject_id","topic_id"] + base_cols[1:]
        opt_cols.remove("topic_id")
    all_cols += opt_cols

    def _val(r: Dict[str,Any], k: str):
        v = r.get(k)
        if k in ("session_date","due_date") and isinstance(v, (date, datetime)):
            return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
        return v

    payload = []
    for r in rows:
        # ensure required
        r.setdefault("lecture_notes","")
        r.setdefault("studio_notes","")
        r.setdefault("completed","")
        r.setdefault("lectures", 0)
        r.setdefault("studios",  0)
        payload.append(tuple(_val(r, c) for c in all_cols))

    placeholders = ",".join("?" for _ in all_cols)
    col_clause  = ",".join(all_cols)
    sql = f"INSERT INTO subject_sessions({col_clause}) VALUES({placeholders})"
    exec_many(sql, payload)

def _delete_sessions(subject_id: int, start: date, end: date):
    exec_one("DELETE FROM subject_sessions WHERE subject_id=? AND session_date BETWEEN ? AND ?",
             (int(subject_id), start.isoformat(), end.isoformat()))

def _stamp_defaults(rows: List[Dict[str, Any]],
                    batch_year: int, semester: int,
                    branch_id: Optional[int], branch_head_id: Optional[int]):
    for r in rows:
        r["batch_year"] = int(batch_year)
        r["semester"]   = int(semester)
        if branch_id is not None:
            r["branch_id"]  = int(branch_id)
        if branch_head_id is not None:
            r["branch_head_faculty_id"] = int(branch_head_id)

def _faculty_ids_for_subject(subject_id: int) -> List[int]:
    """SIC + all mapped faculty ids (lecture + studio)."""
    ids: List[int] = []
    try:
        sc = read_df("SELECT subject_in_charge_id FROM subject_criteria WHERE id=? LIMIT 1", (int(subject_id),))
        if not sc.empty and pd.notna(sc["subject_in_charge_id"].iloc[0]):
            ids.append(int(sc["subject_in_charge_id"].iloc[0]))
    except Exception:
        pass
    try:
        mf = read_df("SELECT DISTINCT faculty_id FROM subject_faculty_map WHERE subject_id=?", (int(subject_id),))
        if not mf.empty:
            ids.extend([int(x) for x in mf["faculty_id"].tolist() if pd.notna(x)])
    except Exception:
        pass
    # unique
    return sorted(set(ids))

def _conflicts_in_ay(faculty_ids: List[int], ay_start: int, d: date, slot: str,
                     subject_id: int) -> List[Dict[str, Any]]:
    """Find clashes for the same date+slot within that Academic Year (Jun..May) for given faculty."""
    if not faculty_ids: return []
    # AY window heuristic: June 1 → May 31
    start = date(ay_start, 6, 1)
    end   = date(ay_start+1, 5, 31)
    # subject_sessions joined with subject_faculty_map and subject_criteria for the AY filter
    conflicts: List[Dict[str,Any]] = []
    try:
        q = read_df(f"""
            SELECT ss.subject_id, ss.session_date, ss.slot, f.id AS faculty_id, f.name AS faculty_name
              FROM subject_sessions ss
              JOIN subject_faculty_map m ON m.subject_id=ss.subject_id
              JOIN faculty f ON f.id=m.faculty_id
             WHERE ss.session_date=? AND LOWER(ss.slot)=LOWER(?)
               AND ss.subject_id<>?
               AND m.faculty_id IN ({",".join("?"*len(faculty_ids))})
               AND ss.session_date BETWEEN ? AND ?
             UNION
            SELECT ss.subject_id, ss.session_date, ss.slot, f2.id AS faculty_id, f2.name AS faculty_name
              FROM subject_sessions ss
              JOIN subject_criteria sc ON sc.id=ss.subject_id
              JOIN faculty f2 ON f2.id=sc.subject_in_charge_id
             WHERE ss.session_date=? AND LOWER(ss.slot)=LOWER(?)
               AND ss.subject_id<>?
               AND sc.subject_in_charge_id IN ({",".join("?"*len(faculty_ids))})
               AND ss.session_date BETWEEN ? AND ?
        """,
        # params (first block) + (second block)
        tuple([d.isoformat(), slot.lower(), int(subject_id), *faculty_ids, start.isoformat(), end.isoformat(),
               d.isoformat(), slot.lower(), int(subject_id), *faculty_ids, start.isoformat(), end.isoformat()])
        )
        for _, r in q.iterrows():
            conflicts.append(dict(
                subject_id=int(r["subject_id"]),
                date=pd.to_datetime(r["session_date"]).date(),
                slot=str(r["slot"]),
                faculty_id=int(r["faculty_id"]),
                faculty_name=str(r["faculty_name"]),
            ))
    except Exception:
        pass
    return conflicts

def _merge_and_save(subject_id: int,
                    sd: date,
                    ed: date,
                    new_rows: List[Dict[str, Any]],
                    batch_year: int,
                    sem_abs: int,
                    branch_id: Optional[int],
                    branch_head_id: Optional[int],
                    action_label: str = "Save"):
    """Replace collisions (same date+slot) and insert new, do notifications & clash checks."""
    if not new_rows:
        return

    # stamp context
    _stamp_defaults(new_rows, batch_year, sem_abs, branch_id, branch_head_id)

    # delete collisions (same subject_id + date + slot)
    for r in new_rows:
        dte  = r["session_date"]
        slot = str(r["slot"]).lower()
        if isinstance(dte, datetime):
            dte = dte.date()
        exec_one("DELETE FROM subject_sessions WHERE subject_id=? AND session_date=? AND LOWER(slot)=LOWER(?)",
                 (int(subject_id), dte.isoformat(), slot))

    # save
    _save_sessions_bulk(new_rows)

    # target shortfall
    try:
        tgt = read_df("""
            SELECT COALESCE(lectures,0) AS t_lec, COALESCE(studios,0) AS t_stu
              FROM subject_criteria WHERE id=? LIMIT 1
        """, (int(subject_id),))
        if not tgt.empty:
            want_lec = int(tgt["t_lec"].iloc[0] or 0)
            want_stu = int(tgt["t_stu"].iloc[0] or 0)
            cur = _sessions_for(subject_id, sd, ed)
            have_lec = int(cur["lectures"].sum()) if not cur.empty else 0
            have_stu = int(cur["studios"].sum())  if not cur.empty else 0
            miss_lec = max(0, want_lec - have_lec)
            miss_stu = max(0, want_stu - have_stu)
            if miss_lec or miss_stu:
                # SIC + principal + branch head
                recips = _faculty_ids_for_subject(subject_id)
                try:
                    pr = read_df("""
                        SELECT f.id
                          FROM faculty_roles fr JOIN faculty f ON f.id=fr.faculty_id
                         WHERE fr.role_name='principal' LIMIT 1
                    """)
                    if not pr.empty:
                        recips.append(int(pr["id"].iloc[0]))
                except Exception:
                    pass
                if branch_head_id: recips.append(int(branch_head_id))
                recips = sorted(set([_safe_int(x) for x in recips if _safe_int(x)]))
                subj = read_df("SELECT code, name FROM subject_criteria WHERE id=? LIMIT 1", (int(subject_id),))
                scode = subj["code"].iloc[0] if not subj.empty else str(subject_id)
                sname = subj["name"].iloc[0] if not subj.empty else ""
                _notif(recips, int(subject_id),
                       (f"[{action_label}] Targets short in {scode} {sname} "
                        f"(batch {int(batch_year)}, sem {int(sem_abs)}): "
                        f"Lectures short {miss_lec}, Studios short {miss_stu}."))
    except Exception:
        pass

    # faculty clash per Academic Year (same date + slot)
    try:
        fac_ids = _faculty_ids_for_subject(subject_id)
        # AY of the *current Year* selection:
        # find year back from absolute semester: sem 1/2 -> y1, 3/4 -> y2 ...
        year_from_sem = (int(sem_abs) + 1) // 2
        ay_start = _ay_for_year_of_batch(int(batch_year), int(year_from_sem))
        for r in new_rows:
            d = r["session_date"]
            slot = str(r["slot"]).lower()
            if isinstance(d, datetime): d = d.date()
            clashes = _conflicts_in_ay(fac_ids, ay_start, d, slot, subject_id)
            if clashes:
                # notify SIC + principal
                recips = []
                try:
                    sc = read_df("SELECT subject_in_charge_id FROM subject_criteria WHERE id=? LIMIT 1",
                                 (int(subject_id),))
                    if not sc.empty and pd.notna(sc["subject_in_charge_id"].iloc[0]):
                        recips.append(int(sc["subject_in_charge_id"].iloc[0]))
                except Exception:
                    pass
                try:
                    pr = read_df("""
                        SELECT f.id
                          FROM faculty_roles fr JOIN faculty f ON f.id=fr.faculty_id
                         WHERE fr.role_name='principal' LIMIT 1
                    """)
                    if not pr.empty: recips.append(int(pr["id"].iloc[0]))
                except Exception:
                    pass
                if branch_head_id: recips.append(int(branch_head_id))
                recips = sorted(set([_safe_int(x) for x in recips if _safe_int(x)]))
                msg = (f"[{action_label}] Faculty clash detected on {d.isoformat()} ({slot}). "
                       f"{len(clashes)} conflict(s) in AY {ay_start}-{ay_start+1}.")
                _notif(recips, int(subject_id), msg)
                st.warning(msg)
    except Exception:
        pass


# ========================== tail weeks UI ==========================

def _tail_weeks_ui(sd: date, ed: date, subject_id: int,
                   batch_year: int, sem_abs: int,
                   branch_id: Optional[int], branch_head_id: Optional[int]):
    if not sd or not ed:
        return
    span_days   = (ed - sd).days + 1
    total_weeks = max(1, (span_days + 6)//7)

    st.divider()
    with st.expander("➕ Tail/custom weeks (different weekdays per week)", expanded=False):
        week_numbers = list(range(1, total_weeks + 1))
        pick_weeks = st.multiselect(
            "Pick week numbers (1 = first week from Start)",
            week_numbers,
            default=([total_weeks-1, total_weeks] if total_weeks >= 2 else week_numbers),
            key="sch_tail_weeks_v4",
        )
        tail_slot = st.selectbox("Slot", ["morning","afternoon","both"], key="sch_tail_slot_v4")
        tail_kind = st.selectbox("Type", ["lecture","studio","both"], key="sch_tail_kind_v4")

        st.caption("Pick weekdays for each selected week:")
        WEEKDAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        d2i = {d:i for i,d in enumerate(WEEKDAYS)}

        per_wk_days = {}
        for w in pick_weeks:
            per_wk_days[w] = st.multiselect(
                f"Week {w} weekdays",
                WEEKDAYS[:6],
                default=[],
                key=f"sch_tail_weekdays_v4_{w}",
            )

        if st.button("Add sessions for selected tail weeks", use_container_width=True, key="sch_tail_add_v4"):
            holi = set(_holidays_between(sd, ed))
            rows: List[Dict[str,Any]] = []
            for w in pick_weeks:
                wk_start = sd + timedelta(days=7*(int(w)-1))
                choose = per_wk_days.get(w, [])
                idxs   = {d2i[d] for d in choose if d in d2i}
                for i in range(7):
                    d = wk_start + timedelta(days=i)
                    if d < sd or d > ed: continue
                    if d.weekday() in idxs and d not in holi:
                        l = 1 if tail_kind in ("lecture","both") else 0
                        s = 1 if tail_kind in ("studio","both") else 0
                        rows.append(dict(
                            subject_id=subject_id,
                            session_date=d,
                            slot=tail_slot,
                            kind=tail_kind,
                            lectures=l,
                            studios=s,
                            lecture_notes="",
                            studio_notes="",
                            assignment_id=None,
                            due_date=None,
                            completed=""
                        ))
            _merge_and_save(subject_id, sd, ed, rows,
                            int(batch_year), int(sem_abs), branch_id, branch_head_id,
                            action_label="Tail/custom weeks")
            st.success(f"Added {len(rows)} session(s).")
            st.rerun()

def _table_exists(name: str) -> bool:
    try:
        df = read_df("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return not df.empty
    except Exception:
        return False

def _branch_from_allocation_or_criteria(subject_id: int, degree_id: int, batch_year: int, sem_abs: int) -> Optional[int]:
    """Prefer Subject Allocation (if present) for this (degree, batch, sem, subject);
       fallback to Subject Criteria.branch_id; else None."""
    # 1) Subject Allocation (if that table exists)
    try:
        if _table_exists("subject_allocation"):
            q = read_df("""
                SELECT branch_id
                  FROM subject_allocation
                 WHERE subject_id=? AND degree_id=? AND batch_year=? AND semester=?
                 LIMIT 1
            """, (int(subject_id), int(degree_id), int(batch_year), int(sem_abs)))
            if not q.empty and pd.notna(q["branch_id"].iloc[0]):
                return int(q["branch_id"].iloc[0])
    except Exception:
        pass
    # 2) Subject Criteria
    try:
        q2 = read_df("SELECT COALESCE(branch_id, NULL) AS branch_id FROM subject_criteria WHERE id=? LIMIT 1",
                     (int(subject_id),))
        if not q2.empty and pd.notna(q2["branch_id"].iloc[0]):
            return int(q2["branch_id"].iloc[0])
    except Exception:
        pass
    return None



# ============================ MAIN PAGE ============================

def render(user: Dict[str,Any]):
    st.title("Schedule")

    # Degrees
    deg = _get_degrees()
    if deg.empty:
        st.info("Create Degrees first.")
        return

    degree_name = st.selectbox("Degree", deg["name"].tolist(), index=0, key="sch_deg")
    degree_id   = int(deg[deg["name"] == degree_name]["id"].iloc[0])
    years_total = int(deg[deg["name"] == degree_name]["duration_years"].iloc[0] or 5)

    # Batch year + label
    default_batch = int(st.session_state.get("sch_batch_year", date.today().year))
    batch_year = st.number_input("Batch start year", min_value=1900, max_value=2100,
                                 value=default_batch, step=1, key="sch_batch_year")
    batch_label = f"{int(batch_year)}–{int(batch_year)+int(years_total)}"
    st.caption(f"Batch: **{batch_label}**")

    # Year (for the batch) and absolute semester(s)
    year = st.selectbox("Year", list(range(1, years_total+1)), index=0, key="sch_year")
    abs_sems = _abs_sems_for_year(int(year), max_sem=2*years_total)
    sem_choice = st.selectbox("Semester", abs_sems, index=0, key="sch_sem")

    # Academic Year for this (batch, year)
    ay_start = _ay_for_year_of_batch(int(batch_year), int(year))
    st.caption(f"Academic Year for this Year: **{ay_start}–{ay_start+1}**")

    principal = _get_principal_name()
    ci_name   = _class_incharge_name(int(degree_id), int(year), int(ay_start))
    bh_name = "—"  # will be set properly AFTER branch is resolved


    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"**Principal:** {principal}")
        st.markdown(f"**Branch Head:** {bh_name}")
    with c2:
        st.markdown(f"**Class In-Charge (Year {int(year)}):** {ci_name}")
        st.markdown(f"**Batch:** {batch_label}")
    with c3:
        st.markdown(f"**Degree:** {degree_name}")
        st.markdown(f"**Semester:** {int(sem_choice)}")

    # Subjects in this context
    subs = _subjects_for(degree_id, int(batch_year), int(sem_choice))
    if subs.empty:
        st.info("No subjects found for Degree/Batch/Semester.")
        return

    sub_display = (subs["code"].fillna("").str.strip() + " — " +
                   subs["name"].fillna("").str.strip()).str.strip(" —")
    sub_display = [s if s.strip(" —") else f"Subject #{int(subs.iloc[i]['id'])}"
                   for i, s in enumerate(sub_display)]
    pick = st.selectbox("Subject", sub_display, index=0, key="sch_subject")
    subject_id = int(subs.iloc[sub_display.index(pick)]["id"])
    subject_code = str(subs.iloc[sub_display.index(pick)]["code"]) if "code" in subs else ""
    subject_name = str(subs.iloc[sub_display.index(pick)]["name"]) if "name" in subs else ""

    # --- Branch & Branch Head (must run AFTER subject_id is known) ---

    # Branch catalogue for this degree (for display/override only)
    br_df = _branch_df_for_degree(degree_id)

    # Prefer stored branch: Subject Allocation → Subject Criteria
    stored_branch_id = _branch_from_allocation_or_criteria(
        int(subject_id), int(degree_id), int(batch_year), int(sem_choice)
    )

    # Who can override
    can_override = str(user.get("role", "")).lower() in ("superadmin", "principal", "director")
    # If we have a stored branch, default is no override. If missing, allow pick to set it.
    override = stored_branch_id is None

    if stored_branch_id is not None and can_override:
       override = st.toggle(
            "Override branch for this schedule context",
            value=False,
            key="sch_branch_override",
        )

    branch_id = None
    branch_name = "—"

    if br_df is not None and not br_df.empty:
        id2name = {int(r["id"]): str(r["name"]) for _, r in br_df.iterrows()}
        name2id = {v: k for k, v in id2name.items()}

        if (not override) and (stored_branch_id in id2name):
            # Read-only display (using stored branch)
            branch_id = int(stored_branch_id)
            branch_name = id2name[branch_id]
            st.markdown(f"**Branch:** {branch_name}  \n*source: Subject Allocation / Criteria*")
        else:
            # Allow selecting a branch (admin override or nothing stored)
            options = br_df["name"].tolist()
            default_name = id2name.get(stored_branch_id) if stored_branch_id in id2name else None
            idx = options.index(default_name) if default_name in options else 0
            branch_name = st.selectbox("Branch", options, index=idx, key="sch_branch")
            branch_id = int(name2id[branch_name])
    else:
        st.warning("No branches defined for this degree (see Branches page).")

    # Branch Head (derived from branch)
    bh_name = _branch_head_name(branch_id)

    # Faculty for subject
    fac = _subject_faculty(subject_id)
    sic_name = fac.get("sic_name", "—")
    lec_names = [n for n in fac.get("lec_names", []) if n and n != sic_name]
    stu_names = [n for n in fac.get("stu_names", []) if n and n != sic_name]

    st.markdown(f"**Subject In-Charge:** {sic_name}")
    st.markdown(f"**Lecture Faculty:** {', '.join(([sic_name] if sic_name!='—' else []) + lec_names) or '—'}")
    st.markdown(f"**Studio Faculty:** {', '.join(([sic_name] if sic_name!='—' else []) + stu_names) or '—'}")

    # Start/End (you can still change; we show an AY-typical default)
    default_sd = date(ay_start, 6, 1)
    default_ed = date(ay_start+1, 5, 31)
    sd = st.date_input("Start date", value=default_sd, key="sch_sd")
    ed = st.date_input("End date", value=default_ed, key="sch_ed")
    if ed < sd:
        st.error("End date cannot be before Start date.")
        return

    # ---- Generate by pattern ----
    st.subheader("Generate by pattern")
    mode = st.radio("Pattern mode", ["Simple (choose weekdays)", "Alternating weeks (A/B)"],
                    horizontal=True, key="sch_mode_main")

    WEEKDAYS = ["Mon","Tue","Wed","Thu","Fri","Sat"]
    holi = set(_holidays_between(sd, ed))

    # Compute span weeks for sliders
    span_days  = (ed - sd).days + 1
    max_weeks  = max(1, (span_days + 6)//7)

    if mode == "Simple (choose weekdays)":
        wd   = st.multiselect("Weekdays", WEEKDAYS, default=["Mon","Wed","Fri"], key="sch_simple_wd")
        weeks_to_generate = st.slider("Number of weeks to generate", 1, max_weeks, max_weeks, 1,
                                      key="sch_simple_weeks",
                                      help="Generates only within the first N weeks from Start date.")
        limit_ed = min(ed, sd + timedelta(days=weeks_to_generate*7 - 1))
        slot = st.selectbox("Slot", ["morning","afternoon","both"], key="sch_simple_slot")
        kind = st.selectbox("Type", ["lecture","studio","both"], key="sch_simple_kind")

        if st.button("Generate (add/replace collisions)", use_container_width=True, key="sch_simple_go"):
            want = set(["Mon","Tue","Wed","Thu","Fri","Sat"].index(d) for d in wd)
            rows: List[Dict[str,Any]] = []
            here = sd
            while here <= limit_ed:
                if here.weekday() in want and here not in holi:
                    l = 1 if kind in ("lecture","both") else 0
                    s = 1 if kind in ("studio","both") else 0
                    rows.append(dict(
                        subject_id=subject_id,
                        session_date=here,
                        slot=slot,
                        kind=kind,
                        lectures=l,
                        studios=s,
                        lecture_notes="",
                        studio_notes="",
                        assignment_id=None,
                        due_date=None,
                        completed=""
                    ))
                here += timedelta(days=1)
            _merge_and_save(subject_id, sd, limit_ed, rows,
                            int(batch_year), int(sem_choice), branch_id, None,
                            action_label="Pattern generate")
            st.success(f"Generated {len(rows)} session(s) over {weeks_to_generate} week(s).")
            st.rerun()

    else:
        cA, cB = st.columns(2)
        with cA:
            wd_A = st.multiselect("Week A weekdays", WEEKDAYS, default=["Mon","Thu"], key="sch_ab_A")
        with cB:
            wd_B = st.multiselect("Week B weekdays", WEEKDAYS, default=["Tue","Fri"], key="sch_ab_B")
        weeks_to_generate_ab = st.slider("Number of weeks to generate (A/B)", 1, max_weeks, max_weeks, 1,
                                         key="sch_ab_weeks",
                                         help="Generates only within the first N weeks from Start date.")
        limit_ed_ab = min(ed, sd + timedelta(days=weeks_to_generate_ab*7 - 1))
        slot_ab = st.selectbox("Slot", ["morning","afternoon","both"], key="sch_ab_slot")
        kind_ab = st.selectbox("Type", ["lecture","studio","both"], key="sch_ab_kind")

        if st.button("Generate A/B (add/replace collisions)", use_container_width=True, key="sch_ab_go"):
            wantA = set(["Mon","Tue","Wed","Thu","Fri","Sat"].index(d) for d in wd_A)
            wantB = set(["Mon","Tue","Wed","Thu","Fri","Sat"].index(d) for d in wd_B)
            rows: List[Dict[str,Any]] = []
            here = sd
            wk_idx = 0
            while here <= limit_ed_ab:
                want = wantA if (wk_idx % 2 == 0) else wantB
                for i in range(7):
                    d = here + timedelta(days=i)
                    if d > limit_ed_ab: break
                    if d.weekday() in want and d not in holi:
                        l = 1 if kind_ab in ("lecture","both") else 0
                        s = 1 if kind_ab in ("studio","both") else 0
                        rows.append(dict(
                            subject_id=subject_id,
                            session_date=d,
                            slot=slot_ab,
                            kind=kind_ab,
                            lectures=l,
                            studios=s,
                            lecture_notes="",
                            studio_notes="",
                            assignment_id=None,
                            due_date=None,
                            completed=""
                        ))
                wk_idx += 1
                here   += timedelta(days=7)
            _merge_and_save(subject_id, sd, limit_ed_ab, rows,
                            int(batch_year), int(sem_choice), branch_id, None,
                            action_label="Pattern A/B")
            st.success(f"Generated {len(rows)} session(s) over {weeks_to_generate_ab} week(s).")
            st.rerun()

    # Tail weeks
    _tail_weeks_ui(sd, ed, subject_id, int(batch_year), int(sem_choice), branch_id, None)

    # Sessions editor
    st.subheader("Sessions")
    sess = _sessions_for(subject_id, sd, ed)

    asn = _assignments_for_subject(subject_id)
    asn_opts = {int(r["id"]): str(r["title"]) for _, r in asn.iterrows()} if not asn.empty else {}

    edf = pd.DataFrame({
        "ID":            (sess["id"] if "id" in sess else []),
        "Date":          (sess["session_date"] if "session_date" in sess else []),
        "Day":           [ _weekday_name(d) for d in (sess["session_date"] if "session_date" in sess else []) ],
        "Slot":          (sess["slot"] if "slot" in sess else []),
        "Type":          (sess["kind"] if "kind" in sess else []),
        "Lectures":      (sess["lectures"] if "lectures" in sess else []),
        "Studios":       (sess["studios"] if "studios" in sess else []),
        "Lecture Notes": (sess["lecture_notes"] if "lecture_notes" in sess else []),
        "Studio Notes":  (sess["studio_notes"] if "studio_notes" in sess else []),
        "Assignment":    (sess["assignment_id"] if "assignment_id" in sess else []),
        "Due":           (sess["due_date"] if "due_date" in sess else []),
        "Completed":     (sess["completed"] if "completed" in sess else []),
    })

    colcfg = {
        "ID":   st.column_config.NumberColumn("ID", disabled=True),
        "Date": st.column_config.DateColumn("Date"),
        "Day":  st.column_config.TextColumn("Day", disabled=True),
        "Slot": st.column_config.SelectboxColumn("Slot", options=["morning","afternoon","both"]),
        "Type": st.column_config.SelectboxColumn("Type", options=["lecture","studio","both"]),
        "Lectures": st.column_config.NumberColumn("Lectures", min_value=0, step=1),
        "Studios":  st.column_config.NumberColumn("Studios",  min_value=0, step=1),
        "Lecture Notes": st.column_config.TextColumn("Lecture Notes"),
        "Studio Notes":  st.column_config.TextColumn("Studio Notes"),
        "Assignment": st.column_config.SelectboxColumn("Assignment",
                        options=list(asn_opts.keys()), required=False),
        "Due": st.column_config.DateColumn("Due"),
        "Completed": st.column_config.SelectboxColumn("Completed",
                        options=["","yes","no","maybe"], required=False),
    }

    edited = st.data_editor(edf, column_config=colcfg, use_container_width=True,
                            hide_index=True, key="sched_editor_v2")

    c1,c2,c3 = st.columns(3)
    with c1:
        if st.button("Save changes", use_container_width=True, key="sched_save"):
            rows: List[Dict[str,Any]] = []
            for _, r in edited.iterrows():
                d = r["Date"]
                if pd.isna(d): continue
                d = pd.to_datetime(d).date()
                knd  = str(r["Type"] or "lecture")
                slot = str(r["Slot"] or "morning")
                lec  = _safe_int(r["Lectures"] or 0, 0)
                stu  = _safe_int(r["Studios"]  or 0, 0)
                ln   = str(r.get("Lecture Notes","") or "")
                sn   = str(r.get("Studio Notes","")  or "")
                a_id = _safe_int(r.get("Assignment"))
                due  = r.get("Due")
                due  = (pd.to_datetime(due).date() if (isinstance(due,(date,datetime)) or pd.notna(due)) else None)
                comp = str(r.get("Completed","") or "")
                if a_id and a_id not in asn_opts:
                    a_id = None
                rows.append(dict(
                    subject_id=subject_id,
                    session_date=d,
                    slot=slot,
                    kind=knd,
                    lectures=lec,
                    studios=stu,
                    lecture_notes=ln,
                    studio_notes=sn,
                    assignment_id=a_id,
                    due_date=due,
                    completed=comp
                ))
            _merge_and_save(subject_id, sd, ed, rows,
                            int(batch_year), int(sem_choice), branch_id, None,
                            action_label="Editor save")
            st.success("Saved.")
            st.rerun()
    with c2:
        if st.button("Delete all in range", use_container_width=True, key="sched_del"):
            _delete_sessions(subject_id, sd, ed)
            st.warning("Deleted all sessions in the visible date range.")
            st.rerun()
    with c3:
        if st.button("Repair legacy semesters", use_container_width=True, key="sched_fix_sem"):
            try:
                exec_one("""
                    UPDATE subject_sessions
                       SET semester=?
                     WHERE subject_id=? AND session_date BETWEEN ? AND ?
                """, (int(sem_choice), int(subject_id), sd.isoformat(), ed.isoformat()))
                st.success("Semesters repaired for the visible range.")
            except Exception as e:
                st.error(f"Repair failed: {e}")
