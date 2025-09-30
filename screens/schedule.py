# screens/schedule.py
from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from core.db import (
    read_df,
    exec_sql,
    exec_many,
    get_conn,
    ensure_base_schema,
)

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def _table_exists(name: str) -> bool:
    try:
        df = read_df(
            "SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)=LOWER(?) LIMIT 1",
            (name,),
        )
        return not df.empty
    except Exception:
        return False

def _column_exists(table: str, column: str) -> bool:
    try:
        with get_conn() as conn:
            c = conn.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in c.fetchall()]
            return column in cols
    except Exception:
        return False

def _holidays_between(sd: date, ed: date) -> List[date]:
    try:
        df = read_df(
            "SELECT date FROM holidays WHERE date BETWEEN ? AND ? ORDER BY date",
            (sd.isoformat(), ed.isoformat()),
        )
        if df.empty:
            return []
        return [datetime.strptime(d, "%Y-%m-%d").date() for d in df["date"].tolist()]
    except Exception:
        return []

def _abs_sems_for_year(year: int) -> Tuple[int, int]:
    """Year 1 -> (1,2), Year 2 -> (3,4), etc."""
    if year <= 0:
        year = 1
    s1 = 2 * (year - 1) + 1
    return s1, s1 + 1

def _branch_df_for_degree(degree_id: int) -> pd.DataFrame:
    return read_df(
        "SELECT id, name, COALESCE(branch_head_faculty_id,NULL) AS bh_id FROM branches WHERE degree_id=? ORDER BY name",
        (int(degree_id),),
    )

def _branch_head_name(branch_id: Optional[int]) -> str:
    if branch_id is None:
        return "—"
    df = read_df("SELECT COALESCE(branch_head_faculty_id,NULL) AS bh FROM branches WHERE id=? LIMIT 1", (int(branch_id),))
    if df.empty or pd.isna(df["bh"].iloc[0]):
        return "—"
    nm = read_df("SELECT name FROM faculty WHERE id=? LIMIT 1", (int(df["bh"].iloc[0]),))
    return nm["name"].iloc[0] if not nm.empty else "—"

def _get_principal_name() -> str:
    # roles table optional; else branding/principal stored elsewhere.
    try:
        df = read_df("SELECT f.name FROM roles r JOIN faculty f ON f.id=r.faculty_id WHERE LOWER(r.role)='principal' LIMIT 1")
        if not df.empty:
            return df["name"].iloc[0]
    except Exception:
        pass
    # fallback from branding set principal_name
    try:
        df = read_df("SELECT COALESCE(principal_name,'') AS p FROM branding LIMIT 1")
        if not df.empty and str(df["p"].iloc[0]).strip():
            return str(df["p"].iloc[0]).strip()
    except Exception:
        pass
    return "—"

def _class_incharge_name(degree_id: int, year: int, ay_start: int) -> str:
    """
    Prefer branches table if it stores CIC per degree/year/ay_start.
    Fallback to cic_assignments if present.
    """
    # Newer schema: branches(degree_id, ay_start, year, class_incharge_faculty_id)
    try:
        if _column_exists("branches", "ay_start") and _column_exists("branches", "year") and _column_exists("branches", "class_incharge_faculty_id"):
            df = read_df("""
                SELECT f.name
                  FROM branches b
                  JOIN faculty f ON f.id=b.class_incharge_faculty_id
                 WHERE b.degree_id=? AND b.ay_start=? AND b.year=?
                 LIMIT 1
            """, (int(degree_id), int(ay_start), int(year)))
            if not df.empty:
                return df["name"].iloc[0]
    except Exception:
        pass
    # Older schema: cic_assignments(degree_id, ay_start, year, faculty_id)
    try:
        if _table_exists("cic_assignments"):
            df = read_df("""
                SELECT f.name
                  FROM cic_assignments c
                  JOIN faculty f ON f.id=c.faculty_id
                 WHERE c.degree_id=? AND c.ay_start=? AND c.year=?
                 ORDER BY c.changed_at DESC LIMIT 1
            """, (int(degree_id), int(ay_start), int(year)))
            if not df.empty:
                return df["name"].iloc[0]
    except Exception:
        pass
    return "—"

def _subject_targets(subject_id: int) -> Tuple[int, int]:
    """
    Return (required_lectures, required_studios) from subject_criteria if present,
    else (0,0).
    """
    cols = ["lectures","studios","lectures_count","studios_count","total_lectures","total_studios"]
    q = "SELECT " + ", ".join([f"COALESCE({c},0) AS {c}" for c in cols]) + " FROM subject_criteria WHERE id=? LIMIT 1"
    df = read_df(q, (int(subject_id),))
    if df.empty:
        return (0, 0)
    # Try several column names, fall back to 0
    req_l = 0
    req_s = 0
    for c in ["lectures","lectures_count","total_lectures"]:
        if c in df.columns:
            req_l = int(df[c].iloc[0] or 0)
            break
    for c in ["studios","studios_count","total_studios"]:
        if c in df.columns:
            req_s = int(df[c].iloc[0] or 0)
            break
    return (req_l, req_s)

def _notifications_supports_seen_by() -> bool:
    return _column_exists("notifications", "seen_by_faculty_id")

def _notify_faculty(faculty_ids: List[int], subject_id: int, message: str) -> None:
    """
    Insert one row per recipient if notifications.seen_by_faculty_id exists;
    else insert a single generic notification row.
    """
    if not _table_exists("notifications"):
        return
    if _notifications_supports_seen_by():
        rows = [(int(subject_id), str(message), int(fid)) for fid in set(faculty_ids)]
        if rows:
            exec_many("INSERT INTO notifications(subject_id, message, seen_by_faculty_id) VALUES(?,?,?)", rows)
    else:
        exec_sql("INSERT INTO notifications(subject_id, message) VALUES(?,?)", (int(subject_id), str(message)))

# -----------------------------------------------------------------------------
# Subjects & Allocations (topics expand into separate rows)
# -----------------------------------------------------------------------------

def _subjects_and_topics_from_allocation(degree_id: int, batch_year: int, abs_sem: int, branch_id: Optional[int]) -> pd.DataFrame:
    """
    Build the subject picker strictly from allocations/offerings:
      - Core subjects (no topics) -> 1 row
      - Elective/CP with topics  -> 1 row *per topic*
    Prefer subject_offerings when present, else fallback to subject_alloc.
    """
    base = """
        SELECT DISTINCT
            so.subject_id,
            so.topic_id,
            COALESCE(sc.code, s.code)         AS subject_code,
            COALESCE(s.name,  sc.code)        AS subject_name,
            st.title                           AS topic_title
        FROM subject_offerings so
        LEFT JOIN subject_criteria sc ON sc.id = so.subject_id
        LEFT JOIN subjects s
               ON s.degree_id = so.degree_id
              AND s.semester  = so.semester
              AND LOWER(COALESCE(s.code,'')) = LOWER(COALESCE(sc.code,''))
        LEFT JOIN subject_topics st ON st.id = so.topic_id
        WHERE so.degree_id=? AND so.batch_year=? AND so.semester=?
    """
    params = [int(degree_id), int(batch_year), int(abs_sem)]
    if branch_id is not None:
        base += " AND IFNULL(so.branch_id,-1)=IFNULL(?, -1)"
        params.append(int(branch_id))
    base += " ORDER BY subject_code, subject_name, topic_title"
    df = read_df(base, tuple(params))

    if df.empty:
        fa = """
            SELECT DISTINCT
                sa.subject_id,
                sa.topic_id,
                COALESCE(sc.code, s.code)      AS subject_code,
                COALESCE(s.name,  sc.code)     AS subject_name,
                st.title                        AS topic_title
            FROM subject_alloc sa
            LEFT JOIN subject_criteria sc ON sc.id = sa.subject_id
            LEFT JOIN subjects s
                   ON s.degree_id = sa.degree_id
                  AND s.semester  = sa.semester
                  AND LOWER(COALESCE(s.code,'')) = LOWER(COALESCE(sc.code,''))
            LEFT JOIN subject_topics st ON st.id = sa.topic_id
            WHERE sa.degree_id=? AND sa.batch_year=? AND sa.semester=?
        """
        params = [int(degree_id), int(batch_year), int(abs_sem)]
        if branch_id is not None:
            fa += " AND IFNULL(sa.branch_id,-1)=IFNULL(?, -1)"
            params.append(int(branch_id))
        fa += " ORDER BY subject_code, subject_name, topic_title"
        df = read_df(fa, tuple(params))

    if df.empty:
        return pd.DataFrame(columns=["subject_id","topic_id","subject_code","subject_name","topic_title","display"])

    df["display"] = df.apply(
        lambda r: f"{(r['subject_code'] or '').strip()} — {(r['subject_name'] or '').strip()}"
                  + (f" [Topic: {str(r['topic_title']).strip()}]" if pd.notna(r["topic_title"]) and str(r["topic_title"]).strip() else ""),
        axis=1
    )
    return df[["subject_id","topic_id","subject_code","subject_name","topic_title","display"]]

def _alloc_id_for(degree_id:int, batch_year:int, abs_sem:int, subject_id:int, topic_id:Optional[int], branch_id:Optional[int]) -> Optional[int]:
    df = read_df("""
        SELECT id FROM subject_alloc
         WHERE degree_id=? AND batch_year=? AND semester=? AND subject_id=?
           AND IFNULL(topic_id,-1)=IFNULL(?, -1)
           AND IFNULL(branch_id,-1)=IFNULL(?, -1)
         LIMIT 1
    """, (int(degree_id), int(batch_year), int(abs_sem), int(subject_id),
          (int(topic_id) if topic_id is not None else None),
          (int(branch_id) if branch_id is not None else None)))
    if df.empty:
        return None
    return int(df["id"].iloc[0])

def _people_for_alloc(alloc_id: int) -> Tuple[Optional[int], List[int], List[int]]:
    if not alloc_id:
        return (None, [], [])
    a = read_df("SELECT sic_faculty_id FROM subject_alloc WHERE id=?", (int(alloc_id),))
    sic = int(a["sic_faculty_id"].iloc[0]) if (not a.empty and pd.notna(a["sic_faculty_id"].iloc[0])) else None
    lect = read_df("SELECT faculty_id FROM subject_alloc_members WHERE alloc_id=? AND role='lecture' ORDER BY faculty_id", (alloc_id,))
    stud = read_df("SELECT faculty_id FROM subject_alloc_members WHERE alloc_id=? AND role='studio'  ORDER BY faculty_id", (alloc_id,))
    return sic, [int(x) for x in (lect["faculty_id"].tolist() if not lect.empty else [])], \
               [int(x) for x in (stud["faculty_id"].tolist() if not stud.empty else [])]

def _branch_from_allocation_or_criteria(subject_id: int, degree_id: int, batch_year: int, sem_abs: int) -> Optional[int]:
    # Subject Allocation first
    try:
        df = read_df("""
            SELECT branch_id
              FROM subject_alloc
             WHERE subject_id=? AND degree_id=? AND batch_year=? AND semester=?
             ORDER BY id DESC LIMIT 1
        """, (int(subject_id), int(degree_id), int(batch_year), int(sem_abs)))
        if not df.empty and pd.notna(df["branch_id"].iloc[0]):
            return int(df["branch_id"].iloc[0])
    except Exception:
        pass
    # Fallback to Criteria
    try:
        df2 = read_df("SELECT COALESCE(branch_id,NULL) AS branch_id FROM subject_criteria WHERE id=? LIMIT 1", (int(subject_id),))
        if not df2.empty and pd.notna(df2["branch_id"].iloc[0]):
            return int(df2["branch_id"].iloc[0])
    except Exception:
        pass
    return None

# -----------------------------------------------------------------------------
# Sessions IO
# -----------------------------------------------------------------------------

def _sessions_for(subject_id:int, topic_id:Optional[int],
                  batch_year:int, sem_abs:int, branch_id:Optional[int],
                  start: date, end: date) -> pd.DataFrame:
    df = read_df("""
        SELECT id, session_date, slot, kind,
               COALESCE(lectures,0) AS lectures,
               COALESCE(studios,0)  AS studios,
               COALESCE(lecture_notes,'') AS lecture_notes,
               COALESCE(studio_notes,'')  AS studio_notes,
               assignment_id,
               due_date,
               COALESCE(completed,'') AS completed
          FROM subject_sessions
         WHERE subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
           AND batch_year=? AND semester=?
           AND IFNULL(branch_id,-1)=IFNULL(?, -1)
           AND session_date BETWEEN ? AND ?
         ORDER BY session_date, id
    """, (int(subject_id), (int(topic_id) if topic_id is not None else None),
          int(batch_year), int(sem_abs),
          (int(branch_id) if branch_id is not None else None),
          start.isoformat(), end.isoformat()))
    return df

def _save_sessions_bulk(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    exec_many("""
        INSERT INTO subject_sessions(
            subject_id, topic_id, session_date, slot, kind,
            lectures, studios, lecture_notes, studio_notes,
            assignment_id, due_date, completed,
            degree_id, batch_year, semester, branch_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [
        (
            int(r["subject_id"]),
            (int(r["topic_id"]) if r.get("topic_id") is not None else None),
            r["session_date"].isoformat(),
            str(r.get("slot","")).lower(),
            str(r.get("kind","")).lower(),
            int(r.get("lectures",0) or 0),
            int(r.get("studios",0)  or 0),
            str(r.get("lecture_notes","") or ""),
            str(r.get("studio_notes","") or ""),
            (int(r["assignment_id"]) if r.get("assignment_id") not in (None,"",pd.NA) else None),
            (r["due_date"].isoformat() if isinstance(r.get("due_date"), date) else (str(r.get("due_date")) if r.get("due_date") else None)),
            str(r.get("completed","") or ""),
            int(r["degree_id"]),
            int(r["batch_year"]),
            int(r["semester"]),
            (int(r["branch_id"]) if r.get("branch_id") is not None else None),
        )
        for r in rows
    ])

def _merge_and_save(subject_id:int, sd:date, ed:date, rows:List[Dict[str,Any]],
                    batch_year:int, sem_abs:int, branch_id:Optional[int],
                    branch_head_id: Optional[int],
                    action_label:str="Pattern generate") -> None:
    """
    Merge policy: delete any existing rows that collide by (subject_id, topic_id, date, slot, batch, sem, branch),
    then insert new ones.
    """
    if not rows:
        return

    # Normalize inputs + add stamping
    ins: List[Dict[str,Any]] = []
    holi = set(_holidays_between(sd, ed))

    for r in rows:
        d = r.get("session_date")
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        if d in holi:
            # safety (generators should already exclude)
            continue

        payload = dict(r)
        payload["degree_id"]  = int(payload.get("degree_id") or 0) or 0
        payload["batch_year"] = int(batch_year)
        payload["semester"]   = int(sem_abs)
        payload["branch_id"]  = (int(branch_id) if branch_id is not None else None)
        ins.append(payload)

    # Delete collisions, then insert
    with get_conn() as conn:
        c = conn.cursor()
        for r in ins:
            c.execute("""
                DELETE FROM subject_sessions
                 WHERE subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
                   AND batch_year=? AND semester=?
                   AND IFNULL(branch_id,-1)=IFNULL(?, -1)
                   AND session_date=? AND LOWER(slot)=LOWER(?)
            """, (int(r["subject_id"]),
                  (int(r["topic_id"]) if r.get("topic_id") is not None else None),
                  int(batch_year), int(sem_abs),
                  (int(branch_id) if branch_id is not None else None),
                  r["session_date"].isoformat(), str(r.get("slot","")).lower()))
        conn.commit()

    _save_sessions_bulk(ins)

    # Shortfall notification (targets vs planned within range)
    reqL, reqS = _subject_targets(subject_id)
    if reqL or reqS:
        df = read_df("""
            SELECT SUM(COALESCE(lectures,0)) AS L, SUM(COALESCE(studios,0)) AS S
              FROM subject_sessions
             WHERE subject_id=? AND batch_year=? AND semester=?
               AND IFNULL(branch_id,-1)=IFNULL(?, -1)
               AND session_date BETWEEN ? AND ?
        """, (int(subject_id), int(batch_year), int(sem_abs),
              (int(branch_id) if branch_id is not None else None),
              sd.isoformat(), ed.isoformat()))
        curL = int(df["L"].iloc[0] or 0) if not df.empty else 0
        curS = int(df["S"].iloc[0] or 0) if not df.empty else 0
        need_lect = max(0, reqL - curL)
        need_stud = max(0, reqS - curS)
        if need_lect or need_stud:
            # recipients: principal + SIC + branch head (ids)
            recipients: List[int] = []
            # principal id (optional)
            try:
                rid = read_df("SELECT faculty_id FROM roles WHERE LOWER(role)='principal' LIMIT 1", ())
                if not rid.empty:
                    recipients.append(int(rid["faculty_id"].iloc[0]))
            except Exception:
                pass
            # branch head id
            if branch_id is not None:
                bh = read_df("SELECT COALESCE(branch_head_faculty_id,NULL) AS f FROM branches WHERE id=?", (int(branch_id),))
                if not bh.empty and pd.notna(bh["f"].iloc[0]):
                    recipients.append(int(bh["f"].iloc[0]))
            # SIC id
            # find alloc id for this context
            alloc_id = _alloc_id_for(int(ins[0]["degree_id"]), int(batch_year), int(sem_abs), int(subject_id),
                                     (int(ins[0].get("topic_id")) if ins[0].get("topic_id") is not None else None),
                                     branch_id)
            if alloc_id:
                sic, _, _ = _people_for_alloc(alloc_id)
                if sic:
                    recipients.append(int(sic))

            subj = read_df("SELECT COALESCE(code,'') AS code FROM subject_criteria WHERE id=? LIMIT 1", (int(subject_id),))
            subject_code = str(subj["code"].iloc[0]) if not subj.empty else ""
            subject_name_df = read_df("""
                SELECT COALESCE(s.name, sc.code) AS nm
                  FROM subject_criteria sc
                  LEFT JOIN subjects s
                         ON s.degree_id=sc.degree_id AND s.semester=sc.semester
                        AND LOWER(COALESCE(s.code,''))=LOWER(COALESCE(sc.code,''))
                 WHERE sc.id=? LIMIT 1
            """, (int(subject_id),))
            subject_name = str(subject_name_df["nm"].iloc[0]) if not subject_name_df.empty else ""

            _notify_faculty(
                recipients,
                int(subject_id),
                f"[{action_label}] Targets short in {subject_code} {subject_name} (batch {int(batch_year)}, sem {int(sem_abs)}): "
                f"Lectures short {need_lect}, Studios short {need_stud}."
            )

# -----------------------------------------------------------------------------
# Tail weeks (custom end weeks)
# -----------------------------------------------------------------------------

def _tail_weeks_ui(sd: date, ed: date, subject_id: int,
                   batch_year: int, sem_abs: int,
                   branch_id: Optional[int], branch_head_id: Optional[int],
                   topic_id: Optional[int], degree_id: int):
    if not sd or not ed:
        return
    span_days   = (ed - sd).days + 1
    total_weeks = max(1, (span_days + 6)//7)

    st.divider()
    with st.expander("➕ Tail/custom weeks (different weekdays per week)", expanded=False):
        st.caption("Use this to add sessions with different weekdays for late weeks (e.g., exam catch-up).")
        week_numbers = list(range(1, total_weeks + 1))
        pick_weeks = st.multiselect(
            "Pick week numbers (1 = first week from Start)",
            week_numbers,
            default=([total_weeks-1, total_weeks] if total_weeks >= 2 else week_numbers),
            key="sch_tail_weeks_v4",
        )
        tail_slot = st.selectbox("Slot", ["morning","afternoon","both"], key="sch_tail_slot_v4")
        tail_kind = st.selectbox("Type", ["lecture","studio","both"], key="sch_tail_kind_v4")

        st.caption("Pick weekdays for each selected week (holidays auto-skipped):")
        WEEKDAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        d2i = {d:i for i,d in enumerate(WEEKDAYS)}

        per_wk_days = {}
        for w in pick_weeks:
            per_wk_days[w] = st.multiselect(
                f"Week {w} weekdays",
                WEEKDAYS[:6],  # Mon-Sat
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
                            topic_id=topic_id,
                            session_date=d,
                            slot=tail_slot,
                            kind=tail_kind,
                            lectures=l,
                            studios=s,
                            lecture_notes="",
                            studio_notes="",
                            assignment_id=None,
                            due_date=None,
                            completed="",
                            degree_id=degree_id,
                        ))
            _merge_and_save(subject_id, sd, ed, rows,
                            int(batch_year), int(sem_abs), branch_id, branch_head_id,
                            action_label="Tail/custom weeks")
            st.success(f"Added {len(rows)} session(s).")
            st.rerun()

# -----------------------------------------------------------------------------
# Render
# -----------------------------------------------------------------------------

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    ensure_base_schema()

    st.header("Schedule")

    # -------------------- Pick Degree / Batch / Year / Semester --------------------
    deg = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    if deg.empty:
        st.info("Please add a Degree first (Degrees page).")
        return

    degree_name = st.selectbox("Degree", deg["name"].tolist(), index=0, key="sch_deg")
    degree_id = int(deg[deg["name"]==degree_name]["id"].iloc[0])
    dur_years = int(deg[deg["name"]==degree_name]["duration_years"].iloc[0])

    # Batch (AY start). If students table exists, suggest from rolls; else default current year
    yrs = read_df("SELECT DISTINCT CAST(SUBSTR(roll,1,4) AS INT) AS byear FROM students WHERE degree_id=? AND LENGTH(roll)>=4 ORDER BY 1 DESC", (degree_id,))
    suggestions = yrs["byear"].tolist() if not yrs.empty else []
    default_batch = int(suggestions[0]) if suggestions else (date.today().year)
    batch_year = st.number_input("Batch / AY start (e.g., 2025 → 2025–26)", 1900, 2100, value=default_batch, step=1, key="sch_batch")
    batch_label = f"{int(batch_year)}–{int(batch_year)+1}"

    # Year & Semester for this degree
    year_name = st.selectbox("Year", [f"Year {i}" for i in range(1, dur_years+1)], index=0, key="sch_year")
    year = int(year_name.split()[-1])
    abs_s1, abs_s2 = _abs_sems_for_year(year)
    sem_choice = st.selectbox("Semester", [abs_s1, abs_s2], index=0, key="sch_sem")

    # -------------------- Subject list from Allocation (topics expanded) -----------
    # Branch depends on subject, but we need branch list anyway for later view/override.
    br_df = _branch_df_for_degree(degree_id)

    # We'll pick subject first (allocation expands topics)
    subs = _subjects_and_topics_from_allocation(degree_id, int(batch_year), int(sem_choice), None)
    if subs.empty:
        st.info("No allocated subjects/topics found for this Degree / Batch / Semester. Please allocate on the Subject Allocation page.")
        return

    pick = st.selectbox("Subject (topics included)", subs["display"].tolist(), index=0, key="sch_subj")
    srow = subs.iloc[subs["display"].tolist().index(pick)]
    subject_id   = int(srow["subject_id"])
    topic_id     = (int(srow["topic_id"]) if pd.notna(srow["topic_id"]) else None)
    subject_code = str(srow["subject_code"] or "")
    subject_name = str(srow["subject_name"] or "")

    # Preferred branch for this subject context (Allocation → Criteria), with admin override
    stored_branch_id = _branch_from_allocation_or_criteria(subject_id, degree_id, int(batch_year), int(sem_choice))
    can_override = str(user.get("role", "")).lower() in ("superadmin","principal","director")
    override = stored_branch_id is None
    branch_id = stored_branch_id

    if not br_df.empty:
        id2name = {int(r["id"]): str(r["name"]) for _, r in br_df.iterrows()}
        name2id = {v:k for k,v in id2name.items()}
        c1, c2 = st.columns([1,2])
        with c1:
            if stored_branch_id is not None and can_override:
                override = st.toggle("Override branch", value=False, key="sch_branch_override")
        with c2:
            if (not override) and (stored_branch_id in id2name):
                st.markdown(f"**Branch:** {id2name[stored_branch_id]}  \n*source: Allocation/Criteria*")
                branch_id = int(stored_branch_id)
            else:
                bpick = st.selectbox("Branch", br_df["name"].tolist(), index=0, key="sch_branch")
                branch_id = int(name2id[bpick])
    else:
        st.warning("No branches defined for this degree.")
        branch_id = None

    # -------------------- Header names (Principal / CI / BH) -----------------------
    ay_start = int(batch_year)
    principal = _get_principal_name()
    ci_name   = _class_incharge_name(int(degree_id), int(year), int(ay_start))
    bh_name   = _branch_head_name(branch_id)

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

    # -------------------- Show SIC / Faculty for this subject/topic ----------------
    alloc_id = _alloc_id_for(degree_id, int(batch_year), int(sem_choice), subject_id, topic_id, branch_id)
    sic_id, lect_ids, stud_ids = _people_for_alloc(alloc_id) if alloc_id else (None, [], [])
    def _names(ids:List[int]) -> str:
        if not ids: return "—"
        df = read_df(f"SELECT id,name FROM faculty WHERE id IN ({','.join(['?']*len(ids))}) ORDER BY name", tuple(ids))
        return ", ".join(df["name"].tolist()) if not df.empty else "—"
    sic_name   = (read_df("SELECT name FROM faculty WHERE id=?",(sic_id,))["name"].iloc[0] if sic_id else "—")
    lect_names = _names(lect_ids + ([sic_id] if sic_id and (sic_id not in lect_ids) else []))
    stud_names = _names(stud_ids + ([sic_id] if sic_id and (sic_id not in stud_ids) else []))

    st.info(f"**Subject In-Charge:** {sic_name}\n\n**Lecture Faculty:** {lect_names}\n\n**Studio Faculty:** {stud_names}")

    # -------------------- Date range (Start/End) -----------------------------------
    # Try to pull default dates from subject_criteria (batch-aware if present)
    def _default_dates() -> Tuple[date,date]:
        # batch aware in subject_criteria?
        df = read_df("""
            SELECT COALESCE(start_date,NULL) AS sd, COALESCE(end_date,NULL) AS ed
              FROM subject_criteria
             WHERE id=? LIMIT 1
        """, (int(subject_id),))
        if not df.empty and pd.notna(df["sd"].iloc[0]) and pd.notna(df["ed"].iloc[0]):
            try:
                sd = pd.to_datetime(df["sd"].iloc[0]).date()
                ed = pd.to_datetime(df["ed"].iloc[0]).date()
                return sd, ed
            except Exception:
                pass
        # fallback: academic year window roughly June..May
        sd = date(int(batch_year), 6, 1)
        ed = date(int(batch_year)+1, 5, 31)
        return sd, ed

    _sd, _ed = _default_dates()
    colA, colB = st.columns(2)
    with colA:
        sd = st.date_input("Start date", value=_sd, key="sch_start")
    with colB:
        ed = st.date_input("End date",   value=_ed, key="sch_end")

    if sd > ed:
        st.error("Start date cannot be after End date.")
        st.stop()

    # -------------------- Generate by Pattern (with weeks slider) ------------------
    st.subheader("Generate by pattern")
    span_days   = (ed - sd).days + 1
    total_weeks = max(1, (span_days + 6)//7)

    pick_weeks_count = st.slider("Number of weeks (from start) to fill", min_value=1, max_value=total_weeks, value=total_weeks, key="sch_weeks_slider")

    mode = st.radio("Pattern mode", ["Simple (choose weekdays)", "Alternating weeks (A/B)"],
                    horizontal=True, key="sch_mode_main")

    WEEKDAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    mon_to_sat = WEEKDAYS[:6]
    holi = set(_holidays_between(sd, ed))

    def _generate_simple(weekdays: List[str], slot: str, kind: str) -> List[Dict[str,Any]]:
        # first N weeks window
        limit_ed = sd + timedelta(days=(pick_weeks_count*7 - 1))
        rows: List[Dict[str,Any]] = []
        picks = {mon_to_sat.index(d) for d in weekdays if d in mon_to_sat}
        d = sd
        while d <= min(ed, limit_ed):
            if d.weekday() in picks and d not in holi:
                rows.append(dict(
                    subject_id=subject_id,
                    topic_id=topic_id,
                    session_date=d,
                    slot=slot,
                    kind=kind,
                    lectures=(1 if kind in ("lecture","both") else 0),
                    studios=(1 if kind in ("studio","both")  else 0),
                    lecture_notes="", studio_notes="",
                    assignment_id=None, due_date=None, completed="",
                    degree_id=degree_id,
                ))
            d += timedelta(days=1)
        return rows

    def _generate_ab(weekA: List[str], weekB: List[str], slot: str, kind: str) -> List[Dict[str,Any]]:
        rows: List[Dict[str,Any]] = []
        picksA = {mon_to_sat.index(d) for d in weekA if d in mon_to_sat}
        picksB = {mon_to_sat.index(d) for d in weekB if d in mon_to_sat}
        w = 0
        cur = sd
        limit_ed = sd + timedelta(days=(pick_weeks_count*7 - 1))
        while cur <= min(ed, limit_ed):
            week_start = sd + timedelta(days=7*w)
            use = picksA if (w % 2 == 0) else picksB
            for i in range(7):
                d = week_start + timedelta(days=i)
                if d < sd or d > min(ed, limit_ed): continue
                if d.weekday() in use and d not in holi:
                    rows.append(dict(
                        subject_id=subject_id,
                        topic_id=topic_id,
                        session_date=d,
                        slot=slot,
                        kind=kind,
                        lectures=(1 if kind in ("lecture","both") else 0),
                        studios=(1 if kind in ("studio","both")  else 0),
                        lecture_notes="", studio_notes="",
                        assignment_id=None, due_date=None, completed="",
                        degree_id=degree_id,
                    ))
            w += 1
            cur = sd + timedelta(days=7*w)
        return rows

    if mode.startswith("Simple"):
        c1, c2 = st.columns([2,1])
        with c1:
            weekdays = st.multiselect("Weekdays", mon_to_sat, default=["Mon","Wed","Fri"], key="sch_simple_wd")
        with c2:
            slot = st.selectbox("Slot", ["morning","afternoon","both"], index=0, key="sch_simple_slot")
            kind = st.selectbox("Type", ["lecture","studio","both"], index=0, key="sch_simple_kind")
        if st.button("Generate", type="primary", key="sch_simple_go"):
            rows = _generate_simple(weekdays, slot, kind)
            _merge_and_save(subject_id, sd, ed, rows, int(batch_year), int(sem_choice), branch_id, None, action_label="Pattern generate")
            st.success(f"Generated {len(rows)} session(s).")
            st.rerun()
    else:
        c1, c2 = st.columns(2)
        with c1:
            weekA = st.multiselect("Week A weekdays", mon_to_sat, default=["Tue","Thu"], key="sch_ab_A")
        with c2:
            weekB = st.multiselect("Week B weekdays", mon_to_sat, default=["Mon","Wed","Fri"], key="sch_ab_B")
        c3, c4 = st.columns(2)
        with c3:
            slot = st.selectbox("Slot", ["morning","afternoon","both"], index=0, key="sch_ab_slot")
        with c4:
            kind = st.selectbox("Type", ["lecture","studio","both"], index=0, key="sch_ab_kind")
        if st.button("Generate (A/B)", type="primary", key="sch_ab_go"):
            rows = _generate_ab(weekA, weekB, slot, kind)
            _merge_and_save(subject_id, sd, ed, rows, int(batch_year), int(sem_choice), branch_id, None, action_label="Pattern generate")
            st.success(f"Generated {len(rows)} session(s).")
            st.rerun()

    # -------------------- Tail/custom weeks ---------------------------------------
    _tail_weeks_ui(sd, ed, subject_id, int(batch_year), int(sem_choice), branch_id, None, topic_id, degree_id)

    # -------------------- Sessions Editor (view/edit/save) -------------------------
    st.subheader("Sessions")
    sess = _sessions_for(subject_id, topic_id, int(batch_year), int(sem_choice), branch_id, sd, ed)

    # Prepare editor dataframe
    def _dow(d: str) -> str:
        try:
            if isinstance(d, date):
                x = d
            else:
                x = pd.to_datetime(d).date()
            return ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][x.weekday()]
        except Exception:
            return ""

    edf = pd.DataFrame({
        "ID":            (sess["id"] if "id" in sess.columns else pd.Series([], dtype="int")),
        "Date":          pd.to_datetime(sess["session_date"]).dt.date if not sess.empty else pd.Series([], dtype="object"),
        "Day":           sess["session_date"].apply(_dow) if not sess.empty else pd.Series([], dtype="object"),
        "Slot":          sess["slot"] if "slot" in sess.columns else pd.Series([], dtype="object"),
        "Type":          sess["kind"] if "kind" in sess.columns else pd.Series([], dtype="object"),
        "Lectures":      sess["lectures"] if "lectures" in sess.columns else pd.Series([], dtype="int"),
        "Studios":       sess["studios"] if "studios" in sess.columns else pd.Series([], dtype="int"),
        "Lecture Notes": sess["lecture_notes"] if "lecture_notes" in sess.columns else pd.Series([], dtype="object"),
        "Studio Notes":  sess["studio_notes"] if "studio_notes" in sess.columns else pd.Series([], dtype="object"),
        "Assignment":    sess["assignment_id"] if "assignment_id" in sess.columns else pd.Series([], dtype="object"),
        "Due":           pd.to_datetime(sess["due_date"]).dt.date if ("due_date" in sess.columns and not sess["due_date"].isna().all()) else pd.Series([], dtype="object"),
        "Completed":     sess["completed"] if "completed" in sess.columns else pd.Series([], dtype="object"),
    })

    edited = st.data_editor(
        edf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "Date": st.column_config.DateColumn("Date"),
            "Day": st.column_config.TextColumn("Day", disabled=True, width="small"),
            "Slot": st.column_config.SelectboxColumn("Slot", options=["morning","afternoon","both"]),
            "Type": st.column_config.SelectboxColumn("Type", options=["lecture","studio","both"]),
            "Lectures": st.column_config.NumberColumn("Lectures", min_value=0, max_value=10, step=1),
            "Studios":  st.column_config.NumberColumn("Studios",  min_value=0, max_value=10, step=1),
            "Lecture Notes": st.column_config.TextColumn("Lecture Notes"),
            "Studio Notes":  st.column_config.TextColumn("Studio Notes"),
            "Assignment":    st.column_config.NumberColumn("Assignment", help="Assignment ID (optional)"),
            "Due":           st.column_config.DateColumn("Due"),
            "Completed":     st.column_config.SelectboxColumn("Completed", options=["","yes","no","maybe"]),
        },
        key="sched_editor_v2",
    )

    # Save edits
    if st.button("💾 Save schedule", type="primary", key="sched_save"):
        try:
            # Convert edited rows -> rows dicts, then merge+save (delete same date/slot then insert)
            rows: List[Dict[str,Any]] = []
            for _, r in edited.iterrows():
                d = r["Date"]
                if pd.isna(d):
                    continue
                if not isinstance(d, date):
                    d = pd.to_datetime(d).date()
                l = int(r.get("Lectures",0) or 0)
                s = int(r.get("Studios",0)  or 0)
                rows.append(dict(
                    subject_id=subject_id,
                    topic_id=topic_id,
                    session_date=d,
                    slot=str(r.get("Slot","") or "").lower(),
                    kind=str(r.get("Type","") or "").lower(),
                    lectures=l,
                    studios=s,
                    lecture_notes=str(r.get("Lecture Notes","") or ""),
                    studio_notes=str(r.get("Studio Notes","") or ""),
                    assignment_id=(int(r["Assignment"]) if pd.notna(r.get("Assignment")) and str(r.get("Assignment")).strip() else None),
                    due_date=(r["Due"] if (not pd.isna(r.get("Due"))) else None),
                    completed=str(r.get("Completed","") or ""),
                    degree_id=degree_id,
                ))
            _merge_and_save(subject_id, sd, ed, rows, int(batch_year), int(sem_choice), branch_id, None, action_label="Editor save")
            st.success("Schedule saved.")
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")

    # Add one day
    with st.expander("➕ Add one day", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            add_date = st.date_input("Date", value=sd, key="sch_add_date")
        with c2:
            add_slot = st.selectbox("Slot", ["morning","afternoon","both"], key="sch_add_slot")
        with c3:
            add_kind = st.selectbox("Type", ["lecture","studio","both"], key="sch_add_kind")
        c4, c5 = st.columns(2)
        with c4:
            add_L = st.number_input("Lectures", min_value=0, max_value=10, value=1 if add_kind in ("lecture","both") else 0, step=1, key="sch_add_L")
        with c5:
            add_S = st.number_input("Studios",  min_value=0, max_value=10, value=1 if add_kind in ("studio","both")  else 0, step=1, key="sch_add_S")
        if st.button("Add", key="sch_add_btn"):
            try:
                if add_date < sd or add_date > ed:
                    st.error("Date must be within Start/End.")
                elif add_date in _holidays_between(sd, ed):
                    st.error("That date is a holiday.")
                else:
                    _merge_and_save(subject_id, sd, ed, [dict(
                        subject_id=subject_id, topic_id=topic_id,
                        session_date=add_date, slot=add_slot, kind=add_kind,
                        lectures=int(add_L), studios=int(add_S),
                        lecture_notes="", studio_notes="",
                        assignment_id=None, due_date=None, completed="",
                        degree_id=degree_id,
                    )], int(batch_year), int(sem_choice), branch_id, None, action_label="Add one day")
                    st.success("Added.")
                    st.rerun()
            except Exception as e:
                st.error(f"Add failed: {e}")

    # Delete one day
    with st.expander("🗑️ Delete one day", expanded=False):
        dates_present = sorted(set(edited["Date"].dropna().tolist())) if not edited.empty else []
        if dates_present:
            col1, col2 = st.columns(2)
            with col1:
                dd = st.selectbox("Date to delete", dates_present, index=0, key="sch_del_date")
            with col2:
                ds = st.selectbox("Slot", ["morning","afternoon","both"], index=0, key="sch_del_slot")
            if st.button("Delete selected day", key="sch_del"):
                try:
                    exec_sql("""
                        DELETE FROM subject_sessions
                         WHERE subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
                           AND batch_year=? AND semester=?
                           AND IFNULL(branch_id,-1)=IFNULL(?, -1)
                           AND session_date=? AND LOWER(slot)=LOWER(?)
                    """, (int(subject_id),
                          (int(topic_id) if topic_id is not None else None),
                          int(batch_year), int(sem_choice),
                          (int(branch_id) if branch_id is not None else None),
                          (dd.isoformat() if isinstance(dd, date) else pd.to_datetime(dd).date().isoformat()),
                          ds.lower()))
                    st.success("Deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")
        else:
            st.caption("No sessions to delete in current range.")

    # Repair legacy semesters (optional helper)
    with st.expander("🛠️ Repair legacy semester values", expanded=False):
        if st.button("Repair", key="sched_fix_sem"):
            try:
                exec_sql("""
                    UPDATE subject_sessions
                       SET semester=?
                     WHERE subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
                       AND batch_year=? AND IFNULL(branch_id,-1)=IFNULL(?, -1)
                       AND (semester IS NULL OR semester<=0)
                """, (int(sem_choice), int(subject_id), (int(topic_id) if topic_id is not None else None),
                      int(batch_year), (int(branch_id) if branch_id is not None else None)))
                st.success("Repaired.")
                st.rerun()
            except Exception as e:
                st.error(f"Repair failed: {e}")

    # -------------------- Export / Import -----------------------------------------
    st.subheader("Import / Export")

    # Export: current context
    export_df = read_df("""
        SELECT id, session_date, slot, kind, lectures, studios, lecture_notes, studio_notes,
               assignment_id, due_date, completed
          FROM subject_sessions
         WHERE subject_id=? AND IFNULL(topic_id,-1)=IFNULL(?, -1)
           AND batch_year=? AND semester=? AND IFNULL(branch_id,-1)=IFNULL(?, -1)
           AND session_date BETWEEN ? AND ?
         ORDER BY session_date, id
    """, (int(subject_id), (int(topic_id) if topic_id is not None else None),
          int(batch_year), int(sem_choice),
          (int(branch_id) if branch_id is not None else None),
          sd.isoformat(), ed.isoformat()))
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export CSV",
        data=csv_bytes,
        file_name=f"schedule_deg{degree_id}_ay{batch_year}_S{int(sem_choice)}_{subject_code}.csv",
        mime="text/csv",
        use_container_width=True,
        key="sch_export_btn"
    )

    # Import
    up = st.file_uploader("Import CSV (same columns as export)", type=["csv"], key="sch_import_file")
    if up is not None:
        try:
            df = pd.read_csv(up)
            # build rows
            rows: List[Dict[str,Any]] = []
            for _, r in df.iterrows():
                d = r.get("session_date") or r.get("Date") or r.get("date")
                if not d: continue
                dd = pd.to_datetime(d).date()
                if dd < sd or dd > ed:  # keep within window
                    continue
                if dd in holi:
                    continue
                slot = str(r.get("slot") or r.get("Slot") or "morning").lower()
                kind = str(r.get("kind") or r.get("Type") or "lecture").lower()
                rows.append(dict(
                    subject_id=subject_id, topic_id=topic_id,
                    session_date=dd, slot=slot, kind=kind,
                    lectures=int(r.get("lectures") if pd.notna(r.get("lectures")) else r.get("Lectures",0)),
                    studios=int(r.get("studios")  if pd.notna(r.get("studios"))  else r.get("Studios",0)),
                    lecture_notes=str(r.get("lecture_notes") if pd.notna(r.get("lecture_notes")) else r.get("Lecture Notes","")),
                    studio_notes=str(r.get("studio_notes")  if pd.notna(r.get("studio_notes"))  else r.get("Studio Notes","")),
                    assignment_id=(int(r.get("assignment_id")) if pd.notna(r.get("assignment_id")) else (int(r.get("Assignment")) if pd.notna(r.get("Assignment")) else None)),
                    due_date=(pd.to_datetime(r.get("due_date")).date() if pd.notna(r.get("due_date")) else (pd.to_datetime(r.get("Due")).date() if (r.get("Due") and not pd.isna(r.get("Due"))) else None)),
                    completed=str(r.get("completed") if pd.notna(r.get("completed")) else r.get("Completed","")),
                    degree_id=degree_id,
                ))
            _merge_and_save(subject_id, sd, ed, rows, int(batch_year), int(sem_choice), branch_id, None, action_label="Import")
            st.success(f"Imported {len(rows)} row(s).")
            st.rerun()
        except Exception as e:
            st.error(f"Import failed: {e}")
