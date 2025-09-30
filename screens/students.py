# screens/students.py
from __future__ import annotations
import re
import pandas as pd
import streamlit as st

from core.db import read_df, get_conn, exec_many, exec_sql
from core.theme import render_theme_css
from core.branding import render_header, render_footer
from core.utils import df_to_csv_bytes

# -------------------------------------------------
# Permissions
# -------------------------------------------------
def _can_edit(role: str) -> bool:
    """
    Per policy:
      - superadmin can add everything
      - class_in_charge can manage students
      - principal/director read-only here
    """
    r = (role or "").strip().lower()
    return r in ("superadmin", "class_in_charge")

# -------------------------------------------------
# Schema guards
# -------------------------------------------------
def _ensure_degrees_table():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS degrees(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                duration_years INTEGER
            )
        """)
        conn.commit()

def _ensure_students_table_and_index():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS students(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll TEXT,
                name TEXT,
                year INTEGER,
                degree TEXT,         -- legacy free-text (kept for back-compat; unused)
                email TEXT,
                batch TEXT,
                degree_id INTEGER
            )
        """)
        # Try to enforce uniqueness on (degree_id, roll) going forward
        try:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_unique ON students(degree_id, roll)")
        except Exception:
            # If duplicates already exist, index creation will fail; user can run the cleanup button below first.
            pass
        conn.commit()

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def _batch_from_roll(roll: str, duration_years: int | None) -> str:
    """
    Batch naming:
    - First 4 digits of roll => join year (e.g., 2022)
    - Batch label => 'YYYY-(YYYY+duration)'
    """
    m = re.match(r"^\s*(\d{4})", str(roll or ""))
    if not m:
        return ""
    start = int(m.group(1))
    dur = int(duration_years or 5)
    return f"{start}-{start + dur}"

def _year_from_roll_first_join(roll: str, duration_years: int | None) -> int | None:
    """
    Estimate current academic year (1..duration) from join year.
    Academic year ticks in June (month >= 6 -> +1).
    """
    m = re.match(r"^\s*(\d{4})", str(roll or ""))
    if not m:
        return None
    join = int(m.group(1))
    now = pd.Timestamp.now()
    years_elapsed = (now.year - join) + (1 if now.month >= 6 else 0)
    dur = int(duration_years or 5)
    years_elapsed = max(1, min(dur, years_elapsed if years_elapsed >= 1 else 1))
    return years_elapsed

# -------------------------------------------------
# Import utilities
# -------------------------------------------------
ROLL_ALIASES  = {"roll no", "roll", "roll_no", "rollno", "reg no", "reg_no"}
NAME_ALIASES  = {"student name", "name", "student", "full name", "fullname"}
EMAIL_ALIASES = {"email", "e-mail", "mail"}
YEAR_ALIASES  = {"year", "yr"}
DEG_ALIASES   = {"degree", "program", "course"}

def _pick_col(cols: set[str], candidates: set[str]) -> str | None:
    for c in candidates:
        if c in cols:
            return c
    return None

def _read_students_file(uploaded) -> pd.DataFrame:
    """
    Read CSV or Excel; normalize headers to lowercase; keep cells as strings; strip whitespace.
    Auto-detect delimiter for CSV.
    """
    name = (uploaded.name or "").lower()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded, dtype=str)
    else:
        try:
            df = pd.read_csv(uploaded, sep=None, engine="python", dtype=str)
        except Exception:
            uploaded.seek(0)
            df = pd.read_csv(uploaded, dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
    return df

# -------------------------------------------------
# Main page
# -------------------------------------------------
def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    _ensure_degrees_table()
    _ensure_students_table_and_index()

    render_theme_css()
    render_header()
    st.header("Students")

    role = (user.get("role") or "").lower()
    editable = _can_edit(role)

    # Degree context
    deg_df = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees ORDER BY name")
    if deg_df.empty:
        st.info("No degrees found. Please add a program in **Degrees / Programs** first.")
        render_footer()
        return

    ctop1, ctop2 = st.columns([2, 1])
    with ctop1:
        deg_names = deg_df["name"].tolist()
        deg_sel = st.selectbox("Degree / Program", deg_names, index=0, key="students_degree")
    with ctop2:
        duration = int(deg_df.loc[deg_df["name"] == deg_sel, "duration_years"].iloc[0])

    deg_row = deg_df[deg_df["name"] == deg_sel].iloc[0]
    degree_id = int(deg_row["id"])

    # -------------------------------------------------
    # Import / Export
    # -------------------------------------------------
    st.markdown("### Import / Export")

    imp_col, exp_col = st.columns([1, 1])

    with imp_col:
        up = st.file_uploader(
            "Import students (CSV or Excel)",
            type=["csv", "xlsx", "xls"],
            disabled=not editable,
            help="Required: Roll No + Student Name. Optional: Email, Year, Degree.",
            key="students_import"
        )
        replace_mode = st.checkbox(
            "Replace existing students for this degree (otherwise append)",
            value=False, disabled=not editable
        )
        skip_dupes = st.checkbox(
            "Skip duplicate rolls (same degree)",
            value=True, disabled=not editable,
            help="Skips rows where the same roll already exists in this degree or repeats within the file."
        )

        if up and editable:
            try:
                df_raw = _read_students_file(up)
                if df_raw.empty:
                    st.error("Could not read any rows from the file.")
                else:
                    cols = set(df_raw.columns)
                    roll_col = _pick_col(cols, ROLL_ALIASES)
                    name_col = _pick_col(cols, NAME_ALIASES)
                    if not roll_col or not name_col:
                        st.error(
                            "Missing required columns.\n\n"
                            f"Looked for Roll in: {sorted(ROLL_ALIASES)}\n"
                            f"and Name in: {sorted(NAME_ALIASES)}"
                        )
                    else:
                        email_col  = _pick_col(cols, EMAIL_ALIASES)
                        year_col   = _pick_col(cols, YEAR_ALIASES)
                        degree_col = _pick_col(cols, DEG_ALIASES)

                        # degree map (CSV degree can override selection if recognized)
                        all_deg = read_df("SELECT id, name, COALESCE(duration_years,5) AS duration_years FROM degrees")
                        deg_map = {
                            str(r["name"]).strip().lower(): (int(r["id"]), int(r["duration_years"]))
                            for _, r in all_deg.iterrows()
                        }

                        # Existing rolls in current degree (for duplicate skipping)
                        existing = read_df("SELECT LOWER(roll) AS roll FROM students WHERE degree_id=?", (degree_id,))
                        existing_rolls = set(existing["roll"].dropna().tolist())
                        seen_in_file = set()

                        rows, skipped = [], []
                        for idx, r in df_raw.iterrows():
                            roll = (r.get(roll_col) or "").strip()
                            name = (r.get(name_col) or "").strip()
                            if not roll or not name:
                                skipped.append((idx + 1, "Missing roll or name"))
                                continue

                            # per-row degree context
                            csv_deg_name = (r.get(degree_col) or "").strip().lower() if degree_col else ""
                            if csv_deg_name and csv_deg_name in deg_map:
                                d_id, dur = deg_map[csv_deg_name]
                            else:
                                d_id, dur = degree_id, duration

                            # de-dupe logic (by roll within degree)
                            key = f"{roll.lower()}::{d_id}"
                            if skip_dupes:
                                if roll.lower() in existing_rolls:
                                    skipped.append((idx + 1, f"Duplicate roll in DB for this degree: {roll}"))
                                    continue
                                if key in seen_in_file:
                                    skipped.append((idx + 1, f"Duplicate roll in this file for this degree: {roll}"))
                                    continue
                                seen_in_file.add(key)

                            batch = _batch_from_roll(roll, dur)

                            # year: prefer CSV explicit; else estimate
                            yy = None
                            if year_col and pd.notna(r.get(year_col)) and str(r.get(year_col)).strip():
                                try:
                                    yy = int(str(r.get(year_col)).strip())
                                except Exception:
                                    skipped.append((idx + 1, f"Bad year value: {r.get(year_col)}"))
                                    yy = None
                            if yy is None:
                                yy = _year_from_roll_first_join(roll, dur)

                            email = None
                            if email_col and pd.notna(r.get(email_col)):
                                e = str(r.get(email_col)).strip()
                                email = e if e else None

                            rows.append((roll, name, yy, None, email, batch, d_id))

                        # Preview
                        st.write("Preview (first 10 parsed rows):")
                        prev_df = pd.DataFrame(
                            rows,
                            columns=["roll", "name", "year", "degree", "email", "batch", "degree_id"]
                        ).head(10)
                        st.dataframe(prev_df, use_container_width=True)

                        if not rows:
                            st.warning(f"No valid rows found. Skipped: {len(skipped)}")
                        else:
                            if replace_mode:
                                exec_sql("DELETE FROM students WHERE degree_id=?", (degree_id,))
                            exec_many(
                                "INSERT INTO students(roll, name, year, degree, email, batch, degree_id) VALUES(?,?,?,?,?,?,?)",
                                rows
                            )
                            # verify how many exist for this degree
                            after = read_df(
                                "SELECT COUNT(*) AS c FROM students WHERE degree_id=?",
                                (degree_id,)
                            )["c"].iloc[0]
                            st.success(
                                f"Imported {len(rows)} students into '{deg_sel}'. "
                                f"{'Skipped ' + str(len(skipped)) + ' rows. ' if skipped else ''}"
                                f"Now this degree has {after} students."
                            )
                            if skipped:
                                with st.expander("Why some rows were skipped?"):
                                    st.dataframe(pd.DataFrame(skipped, columns=["row_number", "reason"]), use_container_width=True)
                            st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    with exp_col:
        df_cur = read_df(
            "SELECT roll AS 'Roll No', name AS 'Student Name', COALESCE(email,'') AS Email, "
            "COALESCE(batch,'') AS Batch, COALESCE(year,'') AS Year "
            "FROM students WHERE degree_id=? ORDER BY roll",
            (degree_id,)
        )
        st.download_button(
            "Export current degree (CSV)",
            data=df_to_csv_bytes(df_cur),
            file_name=f"students_{deg_sel.replace(' ', '_')}.csv",
            mime="text/csv"
        )

    # -------------------------------------------------
    # Add single student
    # -------------------------------------------------
    st.markdown("### Add Student")
    with st.form("add_student", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.2, 1.6, 1.4, 1.1])
        with c1:
            roll_in = st.text_input("Roll No *", placeholder="e.g., 2022ABC001")
        with c2:
            name_in = st.text_input("Student Name *")
        with c3:
            email_in = st.text_input("Email (optional)")
        with c4:
            est_year = _year_from_roll_first_join(roll_in, duration) if roll_in else 1
            year_in = st.number_input("Year (1..duration)", min_value=1, max_value=int(duration),
                                      value=int(est_year or 1), step=1)
        ok_add = st.form_submit_button("Add", disabled=not editable)

    if ok_add:
        if not roll_in.strip() or not name_in.strip():
            st.error("Roll No and Student Name are required.")
        else:
            try:
                batch = _batch_from_roll(roll_in, duration)
                exec_sql(
                    "INSERT INTO students(roll, name, year, degree, email, batch, degree_id) VALUES(?,?,?,?,?,?,?)",
                    (roll_in.strip(), name_in.strip(), int(year_in), None, email_in.strip() or None, batch, degree_id)
                )
                st.success("Student added.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not add student: {e}")

    st.divider()

    # -------------------------------------------------
    # List / filter / delete
    # -------------------------------------------------
    st.markdown("### Current Students")

    q1, q2 = st.columns([2, 1])
    with q1:
        q = st.text_input("Search by roll or name", placeholder="Type to filter…")
    with q2:
        show_count = st.selectbox("Show", [50, 100, 200, 500, 1000], index=1)

    if q.strip():
        like = f"%{q.strip()}%"
        df_list = read_df(
            "SELECT id, roll AS 'Roll No', name AS 'Student Name', COALESCE(email,'') AS Email, "
            "COALESCE(batch,'') AS Batch, COALESCE(year,'') AS Year "
            "FROM students WHERE degree_id=? AND (roll LIKE ? OR name LIKE ?) "
            "ORDER BY roll LIMIT ?",
            (degree_id, like, like, int(show_count))
        )
    else:
        df_list = read_df(
            "SELECT id, roll AS 'Roll No', name AS 'Student Name', COALESCE(email,'') AS Email, "
            "COALESCE(batch,'') AS Batch, COALESCE(year,'') AS Year "
            "FROM students WHERE degree_id=? ORDER BY roll LIMIT ?",
            (degree_id, int(show_count))
        )

    if df_list.empty:
        st.info("No students yet for this degree.")
    else:
        st.dataframe(df_list.drop(columns=["id"]), use_container_width=True)

        if editable:
            st.markdown("#### Delete selected")
            ids = st.multiselect(
                "Pick rows to delete",
                options=df_list["id"].tolist(),
                format_func=lambda i: f"{df_list.loc[df_list['id']==i, 'Roll No'].iloc[0]} — "
                                      f"{df_list.loc[df_list['id']==i, 'Student Name'].iloc[0]}",
            )
            if st.button("Delete", disabled=(len(ids) == 0)):
                try:
                    exec_many("DELETE FROM students WHERE id=?", [(int(x),) for x in ids])
                    st.success(f"Deleted {len(ids)} students.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")

    # -------------------------------------------------
    # Maintenance: remove duplicates
    # -------------------------------------------------
    if editable:
        st.divider()
        st.markdown("### Maintenance")
        if st.button("Remove duplicates (by Roll No within this degree) — keep earliest"):
            dups = read_df("""
                WITH ranked AS (
                  SELECT id, degree_id, LOWER(roll) AS roll, ROW_NUMBER() OVER(
                    PARTITION BY degree_id, LOWER(roll) ORDER BY id ASC
                  ) AS rn
                  FROM students
                )
                SELECT id FROM ranked WHERE rn > 1 AND degree_id=?
            """, (degree_id,))
            if dups.empty:
                st.info("No duplicates found for this degree.")
            else:
                exec_many("DELETE FROM students WHERE id=?", [(int(x),) for x in dups["id"].tolist()])
                st.success(f"Removed {len(dups)} duplicate rows for this degree.")
                st.rerun()

    render_footer()
