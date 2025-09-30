# screens/subject_criteria.py
from __future__ import annotations

import io
from typing import List, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from core.db import read_df, exec_sql, exec_many
from core.theme import render_theme_css
from core.branding import render_header, render_footer

# ------------------------ Constants / helpers ------------------------

EDIT_ROLES = {"superadmin", "principal", "director"}
SUBJECT_TYPES = ["core", "elective", "college_project"]  # shown in add/edit


def _user_can_edit(user: dict) -> bool:
    role = (user or {}).get("role", "").lower()
    return role in EDIT_ROLES or role in {"class_in_charge", "subject_in_charge"}


def _degrees_df():
    return read_df("""
        SELECT id, name, COALESCE(duration_years,5) AS duration_years
        FROM degrees ORDER BY name
    """)


def _subjects_scope_df(degree_id: int, year: int, sem_abs: int):
    return read_df("""
        SELECT id, code, name, COALESCE(subject_type,'core') AS subject_type,
               COALESCE(credits,0) AS credits,
               COALESCE(lectures,0) AS lectures,
               COALESCE(studios,0) AS studios,
               COALESCE(internal_marks,0) AS internal_marks,
               COALESCE(external_exam_marks,0) AS external_exam_marks,
               COALESCE(external_jury_marks,0) AS external_jury_marks,
               default_start_date, default_end_date
          FROM subjects
         WHERE degree_id=? AND year=? AND semester=?
         ORDER BY code, name
    """, (int(degree_id), int(year), int(sem_abs)))


def _subject_row(subject_id: int):
    df = read_df("""
        SELECT id, degree_id, year, semester, code, name,
               COALESCE(subject_type,'core') AS subject_type,
               COALESCE(credits,0) AS credits,
               COALESCE(lectures,0) AS lectures,
               COALESCE(studios,0) AS studios,
               COALESCE(internal_marks,0) AS internal_marks,
               COALESCE(external_exam_marks,0) AS external_exam_marks,
               COALESCE(external_jury_marks,0) AS external_jury_marks,
               default_start_date, default_end_date
          FROM subjects WHERE id=?
    """, (int(subject_id),))
    return None if df.empty else df.iloc[0].to_dict()


def _attainment_row(subject_id: int):
    df = read_df("""
        SELECT subject_id,
               COALESCE(internal_pct,60.0)  AS internal_pct,
               COALESCE(external_pct,40.0)  AS external_pct,
               COALESCE(threshold_internal_pct,50.0) AS threshold_internal_pct,
               COALESCE(threshold_external_pct,40.0) AS threshold_external_pct,
               COALESCE(direct_pct,80.0)    AS direct_pct,
               COALESCE(indirect_pct,20.0)  AS indirect_pct
          FROM subject_attainment WHERE subject_id=?
    """, (int(subject_id),))
    if df.empty:
        return dict(internal_pct=60.0, external_pct=40.0,
                    threshold_internal_pct=50.0, threshold_external_pct=40.0,
                    direct_pct=80.0, indirect_pct=20.0)
    return df.iloc[0].to_dict()


# ------------------------ Save helpers ------------------------

def _save_subject_master(data: dict, subject_id: int | None) -> int:
    if subject_id:
        exec_sql("""
            UPDATE subjects
               SET code=?, name=?, subject_type=?, credits=?, lectures=?, studios=?,
                   internal_marks=?, external_exam_marks=?, external_jury_marks=?,
                   default_start_date=?, default_end_date=?,
                   year=?, semester=?, degree_id=?
             WHERE id=?
        """, (
            data["code"], data["name"], data["subject_type"],
            int(data["credits"] or 0), int(data["lectures"] or 0), int(data["studios"] or 0),
            int(data["internal_marks"] or 0), int(data["external_exam_marks"] or 0), int(data["external_jury_marks"] or 0),
            data["default_start_date"], data["default_end_date"],
            int(data["year"]), int(data["semester"]), int(data["degree_id"]),
            int(subject_id)
        ))
        return int(subject_id)
    else:
        exec_sql("""
            INSERT INTO subjects(code, name, degree_id, year, semester, subject_type,
                                 credits, lectures, studios, internal_marks, external_exam_marks, external_jury_marks,
                                 default_start_date, default_end_date)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["code"], data["name"], int(data["degree_id"]), int(data["year"]), int(data["semester"]),
            data["subject_type"], int(data["credits"] or 0), int(data["lectures"] or 0), int(data["studios"] or 0),
            int(data["internal_marks"] or 0), int(data["external_exam_marks"] or 0), int(data["external_jury_marks"] or 0),
            data["default_start_date"], data["default_end_date"]
        ))
        new_id = read_df("SELECT MAX(id) AS id FROM subjects").iloc[0]["id"]
        return int(new_id)


def _save_attainment(subject_id: int, a: dict):
    exec_sql("""
        INSERT INTO subject_attainment(subject_id, internal_pct, external_pct,
                                       threshold_internal_pct, threshold_external_pct,
                                       direct_pct, indirect_pct)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(subject_id) DO UPDATE SET
            internal_pct=excluded.internal_pct,
            external_pct=excluded.external_pct,
            threshold_internal_pct=excluded.threshold_internal_pct,
            threshold_external_pct=excluded.threshold_external_pct,
            direct_pct=excluded.direct_pct,
            indirect_pct=excluded.indirect_pct
    """, (int(subject_id), float(a["internal_pct"]), float(a["external_pct"]),
          float(a["threshold_internal_pct"]), float(a["threshold_external_pct"]),
          float(a["direct_pct"]), float(a["indirect_pct"])))


# ---------- Import / Duplicate check / Export helpers (catalog) ----------

def _to_int(x, default=0):
    try:
        return int(x) if pd.notna(x) and str(x).strip() != "" else default
    except Exception:
        return default


def _to_float(x, default=0.0):
    try:
        return float(x) if pd.notna(x) and str(x).strip() != "" else default
    except Exception:
        return default


def _degree_name(degree_id: int) -> str:
    q = read_df("SELECT name FROM degrees WHERE id=?", (degree_id,))
    return q["name"].iloc[0] if not q.empty else ""


def export_catalog_per_year_csv_bytes(degree_id: int, year: int) -> bytes:
    abs1, abs2 = (year - 1) * 2 + 1, (year - 1) * 2 + 2
    q = read_df("""
        SELECT
          COALESCE(sc.code, s.code)      AS code,
          COALESCE(sc.name, s.name)      AS name,
          ?                               AS degree,
          ((sc.semester + 1) / 2)         AS year,
          CASE WHEN sc.semester % 2 = 1 THEN 1 ELSE 2 END AS semester,
          sc.credits, sc.lectures, sc.studios,
          sc.internal_pct, sc.external_pct,
          sc.threshold_internal_pct, sc.threshold_external_pct
        FROM subject_criteria sc
        LEFT JOIN subjects s
               ON s.degree_id=sc.degree_id AND s.semester=sc.semester
               AND (LOWER(COALESCE(s.code,'')) = LOWER(COALESCE(sc.code,'')) OR LOWER(s.name)=LOWER(sc.name))
        WHERE sc.degree_id=? AND sc.batch_year IS NULL AND sc.semester IN (?,?)
        ORDER BY sc.semester, COALESCE(sc.code, s.code), COALESCE(sc.name, s.name)
    """, (_degree_name(int(degree_id)), int(degree_id), abs1, abs2))
    order = ["code","name","degree","year","semester","credits","lectures","studios",
             "internal_pct","external_pct","threshold_internal_pct","threshold_external_pct"]
    for col in order:
        if col not in q.columns:
            q[col] = ""
    out = q[order].copy()
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_catalog_all_years_csv_bytes(degree_id: int) -> bytes:
    q = read_df("""
        SELECT
          COALESCE(sc.code, s.code)      AS code,
          COALESCE(sc.name, s.name)      AS name,
          ?                               AS degree,
          ((sc.semester + 1) / 2)         AS year,
          CASE WHEN sc.semester % 2 = 1 THEN 1 ELSE 2 END AS semester,
          sc.credits, sc.lectures, sc.studios,
          sc.internal_pct, sc.external_pct,
          sc.threshold_internal_pct, sc.threshold_external_pct
        FROM subject_criteria sc
        LEFT JOIN subjects s
               ON s.degree_id=sc.degree_id AND s.semester=sc.semester
               AND (LOWER(COALESCE(s.code,'')) = LOWER(COALESCE(sc.code,'')) OR LOWER(s.name)=LOWER(sc.name))
        WHERE sc.degree_id=? AND sc.batch_year IS NULL
        ORDER BY sc.semester, COALESCE(sc.code, s.code), COALESCE(sc.name, s.name)
    """, (_degree_name(int(degree_id)), int(degree_id)))
    order = ["code","name","degree","year","semester","credits","lectures","studios",
             "internal_pct","external_pct","threshold_internal_pct","threshold_external_pct"]
    for col in order:
        if col not in q.columns:
            q[col] = ""
    out = q[order].copy()
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def import_subject_criteria_csv_catalog(file_bytes: bytes) -> tuple[int, list]:
    """
    Import into subject_criteria catalog (batch_year NULL).
    Rule: if external exam + jury marks == 0 → internal_pct=100, external_pct=0.
    Returns (rows_ok, rows_bad[ (row_idx, reason) ]).
    """
    df = pd.read_csv(io.BytesIO(file_bytes))
    cols = {c.strip().lower(): c for c in df.columns}
    required = {
        "code","name","degree","year","semester","subject_type","credits","lectures","studios",
        "internal_marks","external_exam_marks","external_jury_marks",
        "internal_pct","external_pct","threshold_internal_pct","threshold_external_pct",
        "direct_pct","indirect_pct"
    }
    missing = required - set(cols.keys())
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))

    rows_ok, rows_bad = 0, []
    for i, r in df.iterrows():
        degree_name = str(r[cols["degree"]]).strip()
        if not degree_name:
            rows_bad.append((i, "Degree is empty"));  continue

        year    = _to_int(r[cols["year"]])
        sem_rel = _to_int(r[cols["semester"]])
        if sem_rel not in (1,2) or year <= 0:
            rows_bad.append((i, f"Invalid year/semester: year={year}, sem={sem_rel}"));  continue
        abs_sem = (year - 1) * 2 + sem_rel

        credits   = _to_int(r[cols["credits"]])
        lectures  = _to_int(r[cols["lectures"]])
        studios   = _to_int(r[cols["studios"]])
        int_marks = _to_int(r[cols["internal_marks"]])
        ext_exam  = _to_int(r[cols["external_exam_marks"]])
        ext_jury  = _to_int(r[cols["external_jury_marks"]])

        int_pct   = _to_float(r[cols["internal_pct"]])
        ext_pct   = _to_float(r[cols["external_pct"]])
        thr_int   = _to_float(r[cols["threshold_internal_pct"]])
        thr_ext   = _to_float(r[cols["threshold_external_pct"]])

        if (ext_exam + ext_jury) == 0:
            int_pct, ext_pct = 100.0, 0.0

        if abs((int_pct + ext_pct) - 100.0) > 0.01:
            rows_bad.append((i, f"Internal%+External% != 100 ({int_pct}+{ext_pct})"));  continue

        # upsert into catalog (batch NULL); create/resolve degree+subject by code/name
        # degree creation utility:
        qd = read_df("SELECT id FROM degrees WHERE LOWER(name)=LOWER(?) LIMIT 1", (degree_name,))
        if qd.empty:
            exec_sql("INSERT INTO degrees(name, duration_years) VALUES(?, ?)", (degree_name, 5))
            qd = read_df("SELECT id FROM degrees WHERE LOWER(name)=LOWER(?) LIMIT 1", (degree_name,))
        degree_id = int(qd["id"].iloc[0])

        # ensure subject row exists (by code or name) for reference/cross-joins
        code = (str(r[cols["code"]]).strip() or None)
        if code and code.lower() == "nan":
            code = None
        name = str(r[cols["name"]]).strip()

        # get/create subject id within this degree+abs_sem
        if code:
            qsid = read_df("""SELECT id FROM subjects
                               WHERE degree_id=? AND LOWER(COALESCE(code,''))=LOWER(?) AND semester=? LIMIT 1""",
                           (degree_id, code, abs_sem))
        else:
            qsid = read_df("""SELECT id FROM subjects
                               WHERE degree_id=? AND LOWER(name)=LOWER(?) AND semester=? LIMIT 1""",
                           (degree_id, name, abs_sem))
        if qsid.empty:
            exec_sql("INSERT INTO subjects(code, name, semester, degree_id, year) VALUES(?,?,?,?,?)",
                     (code, name, abs_sem, degree_id, (abs_sem+1)//2))
        # upsert subject_criteria row in catalog
        row = read_df("""
            SELECT id FROM subject_criteria
             WHERE degree_id=? AND semester=? AND batch_year IS NULL
               AND (LOWER(COALESCE(code,''))=LOWER(COALESCE(?,'')) OR LOWER(name)=LOWER(?))
             LIMIT 1
        """, (degree_id, abs_sem, code or "", name))
        if row.empty:
            exec_sql("""
                INSERT INTO subject_criteria(
                    degree_id, batch_year, semester, code, name,
                    credits, lectures, studios,
                    internal_pct, external_pct,
                    threshold_internal_pct, threshold_external_pct
                ) VALUES(?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (degree_id, abs_sem, code or None, name,
                  credits or 0, lectures or 0, studios or 0,
                  int_pct or 0.0, ext_pct or 0.0, thr_int or 0.0, thr_ext or 0.0))
        else:
            exec_sql("""
                UPDATE subject_criteria
                   SET credits=?, lectures=?, studios=?,
                       internal_pct=?, external_pct=?,
                       threshold_internal_pct=?, threshold_external_pct=?
                 WHERE id=?
            """, (credits or 0, lectures or 0, studios or 0,
                  int_pct or 0.0, ext_pct or 0.0, thr_int or 0.0, thr_ext or 0.0,
                  int(row["id"].iloc[0])))
        rows_ok += 1

    return rows_ok, rows_bad


def find_catalog_duplicates(degree_id: int) -> pd.DataFrame:
    dup = read_df("""
        WITH base AS (
          SELECT id, degree_id, semester, LOWER(COALESCE(code,'')) AS lcode,
                 LOWER(name) AS lname, code, name, created
          FROM (
            SELECT sc.*, datetime(id, 'unixepoch') AS created
            FROM subject_criteria sc
          ) t
          WHERE degree_id=? AND batch_year IS NULL
        ),
        c_group AS (
          SELECT semester, lcode, COUNT(*) c
          FROM base WHERE lcode <> ''
          GROUP BY semester, lcode HAVING c > 1
        ),
        n_group AS (
          SELECT semester, lname, COUNT(*) c
          FROM base WHERE lcode = ''
          GROUP BY semester, lname HAVING c > 1
        )
        SELECT b.*
        FROM base b
        JOIN (
          SELECT semester, lcode AS keyv FROM c_group
          UNION ALL
          SELECT semester, lname AS keyv FROM n_group
        ) g ON (
          (b.lcode <> '' AND g.keyv = b.lcode AND g.semester=b.semester) OR
          (b.lcode = ''  AND g.keyv = b.lname AND g.semester=b.semester)
        )
        ORDER BY b.semester, b.id
    """, (degree_id,))
    return dup


def dedupe_catalog_keep_latest(degree_id: int) -> tuple[int,int]:
    dup = find_catalog_duplicates(degree_id)
    if dup.empty:
        return (0, 0)

    def key_row(r):
        if str(r["code"] or "").strip() != "":
            return (int(r["semester"]), "c", str(r["code"]).strip().lower())
        else:
            return (int(r["semester"]), "n", str(r["name"]).strip().lower())

    groups = {}
    for _, r in dup.iterrows():
        k = key_row(r)
        groups.setdefault(k, []).append(int(r["id"]))

    keep, delete = [], []
    for _, ids in groups.items():
        ids_sorted = sorted(ids)
        keep_id = ids_sorted[-1]
        keep.append(keep_id)
        delete_ids = ids_sorted[:-1]
        delete.extend(delete_ids)

    if delete:
        exec_many("DELETE FROM subject_criteria WHERE id=?", [(i,) for i in delete])

    return (len(keep), len(delete))


def render_import_export_panel(degree_id: int):
    st.markdown("### Import / Duplicate Check / Export")

    with st.expander("📥 Import Subject Criteria (catalog — no batch)", expanded=False):
        up = st.file_uploader("Upload CSV", type=["csv"], key="sc_import_csv")
        st.caption("Required columns: code,name,degree,year,semester,subject_type,credits,lectures,studios,"
                   " internal_marks,external_exam_marks,external_jury_marks,"
                   " internal_pct,external_pct,threshold_internal_pct,threshold_external_pct,direct_pct,indirect_pct")
        if up is not None and st.button("Import CSV", use_container_width=True):
            try:
                ok, bad = import_subject_criteria_csv_catalog(up.read())
                if bad:
                    st.warning(f"Imported {ok}, skipped {len(bad)}.")
                    for i, msg in bad[:20]:
                        st.write(f"• Row {i+1}: {msg}")
                else:
                    st.success(f"Imported {ok} rows.")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    with st.expander("🔎 Find & Fix Duplicates (catalog only)", expanded=False):
        if st.button("Scan duplicates", key="sc_scan_dups"):
            dups = find_catalog_duplicates(int(degree_id))
            if dups.empty:
                st.success("No duplicates found.")
            else:
                st.warning(f"Found {len(dups)} duplicates; older entries will be removed if you confirm.")
                st.dataframe(dups[["id","semester","code","name"]], use_container_width=True)
                if st.button("De-duplicate (keep latest)", type="primary", key="sc_dedupe_go"):
                    kept, deleted = dedupe_catalog_keep_latest(int(degree_id))
                    st.success(f"Done. Kept {kept}, deleted {deleted}.")
                    st.rerun()

    with st.expander("📤 Export Catalog", expanded=False):
        # duration chooses the max year for single-year export control
        dur = int(read_df("SELECT duration_years FROM degrees WHERE id=?", (int(degree_id),))["duration_years"].iloc[0])
        y = st.number_input("Export single Year", min_value=1, max_value=dur, value=1, step=1, key="sc_exp_year")
        c1, c2 = st.columns(2)
        with c1:
            if st.button(f"Export Year {y} (CSV)", key="sc_exp_y_btn"):
                data = export_catalog_per_year_csv_bytes(int(degree_id), int(y))
                st.download_button(
                    label=f"Download Year {int(y)} CSV",
                    data=data,
                    file_name=f"subject_catalog_year_{int(y)}.csv",
                    mime="text/csv",
                    key="sc_exp_y_dl",
                    use_container_width=True
                )
        with c2:
            if st.button("Export ALL Years (CSV)", key="sc_exp_all_btn"):
                data = export_catalog_all_years_csv_bytes(int(degree_id))
                st.download_button(
                    label="Download All Years CSV",
                    data=data,
                    file_name="subject_catalog_all_years.csv",
                    mime="text/csv",
                    key="sc_exp_all_dl",
                    use_container_width=True
                )


# ------------------------ UI (trimmed after Attainment) ------------------------

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    render_theme_css()
    render_header()
    st.header("Subject Criteria")

    # -------- Scope pickers --------
    deg_df = _degrees_df()
    if deg_df.empty:
        st.info("Please create a Degree/Program first (Degrees page).")
        render_footer(); return

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        deg_pick = st.selectbox("Degree / Program", deg_df["name"].tolist(), index=0, key="sc_deg")
        degree_id = int(deg_df[deg_df["name"] == deg_pick]["id"].iloc[0])
        duration = int(deg_df[deg_df["name"] == deg_pick]["duration_years"].iloc[0])

    with c2:
        year = st.number_input("Year", min_value=1, max_value=duration, value=1, step=1, key="sc_year")

    # Absolute semester mapping: Y1 -> 1/2, Y2 -> 3/4, ...
    abs_sem_1 = (int(year) - 1) * 2 + 1
    abs_sem_2 = abs_sem_1 + 1
    max_abs_sem = duration * 2
    sem_options = [s for s in (abs_sem_1, abs_sem_2) if 1 <= s <= max_abs_sem]
    with c3:
        sem_abs = st.selectbox("Semester", sem_options, index=0, key="scope_semester")

    st.divider()
    # Import / Dedupe / Export
    render_import_export_panel(int(degree_id))
    st.divider()

    can_edit = _user_can_edit(user)

    st.subheader("Subjects in scope")
    scope_df = _subjects_scope_df(degree_id, int(year), int(sem_abs))
    st.dataframe(
        scope_df[["code","name","subject_type","credits","lectures","studios"]]
        if not scope_df.empty else
        pd.DataFrame(columns=["code","name","subject_type","credits","lectures","studios"]),
        use_container_width=True,
    )

    # ---------------- Add/Edit (master) ----------------
    with st.expander("➕ Add / Edit a subject", expanded=False):
        names = ["— New —"] + [f"{(r['code'] or '').strip()} · {r['name']}" for _, r in scope_df.iterrows()]
        pick = st.selectbox("Select", names, index=0, key="sc_edit_pick")
        existing = None
        if pick != "— New —":
            row = scope_df.iloc[names.index(pick) - 1]
            existing = _subject_row(int(row["id"]))

        # form fields
        code = st.text_input("Code", value=(existing or {}).get("code", ""))
        name = st.text_input("Name", value=(existing or {}).get("name", ""))
        s_type = st.selectbox("Type", SUBJECT_TYPES, index=SUBJECT_TYPES.index((existing or {}).get("subject_type","core")))
        cA, cB, cC = st.columns(3)
        with cA:
            credits  = st.number_input("Credits",  min_value=0, value=int((existing or {}).get("credits",0)), step=1)
        with cB:
            lectures = st.number_input("Lectures", min_value=0, value=int((existing or {}).get("lectures",0)), step=1)
        with cC:
            studios  = st.number_input("Studios",  min_value=0, value=int((existing or {}).get("studios",0)), step=1)

        m1, m2, m3 = st.columns(3)
        with m1:
            internal_marks = st.number_input("Internal Marks", min_value=0, value=int((existing or {}).get("internal_marks",0)), step=1)
        with m2:
            external_exam_marks = st.number_input("External Exam Marks", min_value=0, value=int((existing or {}).get("external_exam_marks",0)), step=1)
        with m3:
            external_jury_marks = st.number_input("External Jury/Viva Marks", min_value=0, value=int((existing or {}).get("external_jury_marks",0)), step=1)

        d1, d2 = st.columns(2)
        with d1:
            d_start = st.date_input("Default Start Date", value=(pd.to_datetime((existing or {}).get("default_start_date")).date()
                                                                 if existing and existing.get("default_start_date") else None))
        with d2:
            d_end   = st.date_input("Default End Date", value=(pd.to_datetime((existing or {}).get("default_end_date")).date()
                                                               if existing and existing.get("default_end_date") else None))

        # save master
        if can_edit and st.button("Save Subject", type="primary"):
            payload = dict(
                code=code.strip() or None,
                name=name.strip(),
                subject_type=s_type,
                credits=int(credits or 0),
                lectures=int(lectures or 0),
                studios=int(studios or 0),
                internal_marks=int(internal_marks or 0),
                external_exam_marks=int(external_exam_marks or 0),
                external_jury_marks=int(external_jury_marks or 0),
                default_start_date=d_start.isoformat() if d_start else None,
                default_end_date=d_end.isoformat() if d_end else None,
                degree_id=int(degree_id),
                year=int(year),
                semester=int(sem_abs),
            )
            sid = _save_subject_master(payload, existing.get("id") if existing else None)
            st.success(f"Saved subject (ID {sid}).")
            st.rerun()

    # ---------------- Attainment Settings ONLY ----------------
    st.subheader("Attainment Settings (per subject)")

    # pick a subject from scope to edit attainment
    if scope_df.empty:
        st.info("No subjects in this scope yet.")
    else:
        subj_label = [f"{(r['code'] or '').strip()} · {r['name']}" for _, r in scope_df.iterrows()]
        idx = st.selectbox("Choose a subject", list(range(len(subj_label))), format_func=lambda i: subj_label[i], key="sc_att_pick")
        subj_id = int(scope_df.iloc[idx]["id"])
        a = _attainment_row(subj_id)

        # If external marks are 0 → force 100/0
        ext_total_marks = int(scope_df.iloc[idx]["external_exam_marks"] or 0) + int(scope_df.iloc[idx]["external_jury_marks"] or 0)
        lock_internal_100 = (ext_total_marks == 0)

        c1, c2 = st.columns(2)
        with c1:
            internal_pct = st.number_input("Internal % (of total)", min_value=0.0, max_value=100.0,
                                           value=float(a["internal_pct"] if not lock_internal_100 else 100.0), step=1.0,
                                           disabled=lock_internal_100)
        with c2:
            external_pct = st.number_input("External % (of total)", min_value=0.0, max_value=100.0,
                                           value=float(a["external_pct"] if not lock_internal_100 else 0.0), step=1.0,
                                           disabled=lock_internal_100)

        if not lock_internal_100 and abs((internal_pct + external_pct) - 100.0) > 0.01:
            st.error("Internal % + External % must equal 100.")

        t1, t2 = st.columns(2)
        with t1:
            thr_int = st.number_input("Threshold Internal %", min_value=0.0, max_value=100.0,
                                      value=float(a["threshold_internal_pct"]), step=1.0)
        with t2:
            thr_ext = st.number_input("Threshold External %", min_value=0.0, max_value=100.0,
                                      value=float(a["threshold_external_pct"]), step=1.0,
                                      disabled=lock_internal_100)

        d1, d2 = st.columns(2)
        with d1:
            direct_pct = st.number_input("Direct Attainment %", min_value=0.0, max_value=100.0,
                                         value=float(a["direct_pct"]), step=1.0)
        with d2:
            indirect_pct = st.number_input("Indirect Attainment %", min_value=0.0, max_value=100.0,
                                           value=float(a["indirect_pct"]), step=1.0)

        if can_edit and st.button("Save Attainment"):
            if lock_internal_100:
                internal_pct_save, external_pct_save = 100.0, 0.0
            else:
                if abs((internal_pct + external_pct) - 100.0) > 0.01:
                    st.error("Please fix percentages to total 100 before saving.")
                    st.stop()
                internal_pct_save, external_pct_save = internal_pct, external_pct

            _save_attainment(subj_id, dict(
                internal_pct=internal_pct_save,
                external_pct=external_pct_save,
                threshold_internal_pct=thr_int,
                threshold_external_pct=(0.0 if lock_internal_100 else thr_ext),
                direct_pct=direct_pct,
                indirect_pct=indirect_pct
            ))
            st.success("Attainment settings saved.")

    render_footer()
