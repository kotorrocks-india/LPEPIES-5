# core/db.py
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

DB_PATH = Path("eplp.db")


# ------------------------------ Connection ------------------------------

@contextlib.contextmanager
def get_conn():
    """Yield a SQLite connection with Row factory. Use `with get_conn() as conn:` everywhere."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


# ------------------------------ Helpers ---------------------------------

def read_df(sql: str, params: Sequence | None = None) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params or ())

def exec_one(sql: str, params: Sequence | None = None) -> None:
    with get_conn() as conn:
        conn.execute(sql, params or ())

def exec_many(sql: str, rows: Iterable[Sequence]) -> None:
    rows = list(rows)
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(sql, rows)

# --- Back-compat helpers expected by older modules ---

def exec_sql(sql: str, params: Sequence | None = None) -> None:
    """Back-compat alias for simple statements that don't need results."""
    exec_one(sql, params or ())

def exec_sql_fetchone(sql: str, params: Sequence | None = None):
    """Run a query and return a single row (sqlite3.Row or None)."""
    with get_conn() as conn:
        cur = conn.execute(sql, params or ())
        return cur.fetchone()

def exec_sql_fetchall(sql: str, params: Sequence | None = None):
    """Run a query and return all rows (list of sqlite3.Row)."""
    with get_conn() as conn:
        cur = conn.execute(sql, params or ())
        return cur.fetchall()


# ------------------------- Academic Year helpers ------------------------

# Academic-year helpers
def compute_ay_start_year(batch_year: int, year: int) -> int:
    """
    AY start given batch and year number.
    Example: batch 2021, year 4 -> AY start 2024 (AY 2024–25).
    """
    return int(batch_year) + max(0, int(year) - 1)

def compute_ay_start_abs_sem(batch_year: int, abs_sem: int) -> int:
    """
    AY start given batch and absolute semester.
    Sem 1–2 -> batch
    Sem 3–4 -> batch+1
    ...
    """
    sem = max(1, int(abs_sem))
    return int(batch_year) + ( (sem - 1) // 2 )

# --- Back-compat aliases (used by older pages) ---
def compute_ay_start(batch_year: int, year: int) -> int:
    return compute_ay_start_year(batch_year, year)

def academic_year_start(batch_year: int, abs_sem: int) -> int:
    return compute_ay_start_abs_sem(batch_year, abs_sem)



# --------------------------- Base Schema --------------------------------
# Keep base tables minimal; evolving columns/indexes are added in migrations.

def ensure_base_schema() -> None:
    """Create baseline tables if missing. Idempotent and safe on legacy DBs."""
    with get_conn() as c:
        cur = c.cursor()

        # degrees
        cur.execute("""
        CREATE TABLE IF NOT EXISTS degrees(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            duration_years  INTEGER NOT NULL DEFAULT 5
        )
        """)

        # branches (per-degree) — AY-scoped columns added in migrations
        cur.execute("""
        CREATE TABLE IF NOT EXISTS branches(
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            degree_id INTEGER NOT NULL,
            name      TEXT NOT NULL,
            UNIQUE(degree_id, name)
        )
        """)

        # faculty
        cur.execute("""
        CREATE TABLE IF NOT EXISTS faculty(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            type            TEXT,        -- 'core' / 'visiting'
            title           TEXT,        -- optional display title (Dr/Prof)
            designation     TEXT,        -- Assistant/Associate/Professor, etc.
            credit_limit    INTEGER      -- optional per-designation limit override
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_faculty_name ON faculty(name)")

        # which degrees a faculty belongs to (optional)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS faculty_degrees(
            faculty_id  INTEGER NOT NULL,
            degree_id   INTEGER NOT NULL,
            UNIQUE(faculty_id, degree_id)
        )
        """)

        # faculty roles (principal/director… generic)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS faculty_roles(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            role_name   TEXT NOT NULL,            -- 'principal','director','branch_head','class_in_charge', etc.
            faculty_id  INTEGER NOT NULL,
            slot        INTEGER,                  -- optional: branch/year slot when needed
            UNIQUE(role_name, faculty_id, COALESCE(slot, -1))
        )
        """)

        # users
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT NOT NULL UNIQUE,
            password_hash   TEXT NOT NULL,
            role            TEXT NOT NULL,              -- 'superadmin','principal','director','branch_head','class_in_charge','subject_in_charge','subject_faculty','student'
            faculty_id      INTEGER,                    -- if user is a faculty
            student_roll    TEXT                        -- if user is a student
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

        # students
        cur.execute("""
        CREATE TABLE IF NOT EXISTS students(
            roll        TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            degree_id   INTEGER,
            email       TEXT,
            batch_year  INTEGER,    -- derived from roll's first 4 digits (first student)
            year        INTEGER     -- current year (1..N) – optional; can be computed
        )
        """)

        # branding
        cur.execute("""
        CREATE TABLE IF NOT EXISTS branding(
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            app_name        TEXT,
            logo_path       TEXT,
            footer          TEXT,
            login_bg_path   TEXT
        )
        """)
        cur.execute("INSERT OR IGNORE INTO branding(id, app_name, footer) VALUES(1, 'EPLP Manager', '© Your Name')")

        # theme
        cur.execute("""
        CREATE TABLE IF NOT EXISTS theme_settings(
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            theme_mode          TEXT,       -- 'light'/'dark'
            font_family         TEXT,
            base_fg             TEXT,
            base_bg             TEXT,
            accent              TEXT,
            pill_bg             TEXT,
            pill_fg             TEXT,
            button_bg           TEXT,
            button_fg           TEXT,
            header_bg           TEXT,
            header_fg           TEXT
        )
        """)

        # holidays
        cur.execute("""
        CREATE TABLE IF NOT EXISTS holidays(
            date    TEXT PRIMARY KEY,  -- 'YYYY-MM-DD'
            title   TEXT NOT NULL
        )
        """)

        # subjects (master list; sheet1-like minimal)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subjects(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT,
            name        TEXT,
            semester    INTEGER,
            degree_id   INTEGER
        )
        """)

        # subject_criteria (catalog & optional batch overrides)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_criteria(
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            degree_id               INTEGER NOT NULL,
            semester                INTEGER NOT NULL,          -- absolute 1..(2*duration)
            code                    TEXT,
            name                    TEXT
        )
        """)

        # per-subject topics (electives/CP) – base definition (offering-specific mapping added below)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_topics(
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id          INTEGER NOT NULL,
            topic_code          TEXT,
            title               TEXT,
            capacity            INTEGER,
            mentor_faculty_id   INTEGER
        )
        """)

        # schedule sessions – minimal; evolving columns handled by migrations
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_sessions(
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id              INTEGER NOT NULL,
            session_date            TEXT NOT NULL
        )
        """)

        # notifications (clashes, target shortfalls, etc.)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications(
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT DEFAULT (datetime('now')),
            subject_id          INTEGER,
            message             TEXT NOT NULL
        )
        """)

        # class-in-charge change logs
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cic_change_log(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            changed_at      TEXT DEFAULT (datetime('now')),
            degree_id       INTEGER NOT NULL,
            ay_start        INTEGER NOT NULL,
            year            INTEGER NOT NULL,
            from_faculty_id INTEGER,
            to_faculty_id   INTEGER,
            changed_by      TEXT
        )
        """)

        # ---------- NEW: Subject Allocation (per batch/AY/branch) ----------
        # One offering per subject/batch/abs_sem (+ optional branch)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_offerings(
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id              INTEGER NOT NULL,
            degree_id               INTEGER NOT NULL,
            batch_year              INTEGER NOT NULL,
            semester                INTEGER NOT NULL,      -- absolute semester
            branch_id               INTEGER,               -- optional (if subject is branch-scoped)
            subject_in_charge_id    INTEGER,               -- SIC for this offering
            academic_year_start     INTEGER NOT NULL,      -- e.g., 2024 for AY 2024–25
            UNIQUE(subject_id, batch_year, semester, COALESCE(branch_id, -1))
        )
        """)

        # Faculty attached to an offering (lecture/studio/other)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_offering_faculty(
            offering_id     INTEGER NOT NULL,
            faculty_id      INTEGER NOT NULL,
            role            TEXT NOT NULL,                 -- 'lecture'|'studio'|'other'
            UNIQUE(offering_id, faculty_id, role)
        )
        """)

        # Topics opened for an offering (electives/CP)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS subject_topic_offerings(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            offering_id     INTEGER NOT NULL,
            topic_id        INTEGER NOT NULL,
            capacity        INTEGER
        )
        """)

        # Student’s chosen topic per offering (one selection per student per offering)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS student_topic_choices(
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            offering_id     INTEGER NOT NULL,
            topic_id        INTEGER NOT NULL,
            student_roll    TEXT NOT NULL,
            chosen_at       TEXT DEFAULT (datetime('now')),
            UNIQUE(offering_id, student_roll)
        )
        """)

    # Run migrations AFTER creating minimal tables
    run_light_migrations()


# ------------------------ Light Migrations --------------------------

def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in info} if info else set()

def run_light_migrations() -> None:
    """Add columns/indexes that newer pages expect. Idempotent and safe on existing DBs."""
    with get_conn() as c:
        # subject_sessions evolving columns
        have = _table_cols(c, "subject_sessions")
        add_cols = [
            ("topic_id",                "INTEGER"),
            ("slot",                    "TEXT"),
            ("kind",                    "TEXT"),
            ("lectures",                "INTEGER DEFAULT 0"),
            ("studios",                 "INTEGER DEFAULT 0"),
            ("lecture_notes",           "TEXT"),
            ("studio_notes",            "TEXT"),
            ("assignment_id",           "INTEGER"),
            ("due_date",                "TEXT"),
            ("completed",               "TEXT"),
            ("batch_year",              "INTEGER"),
            ("semester",                "INTEGER"),
            ("branch_id",               "INTEGER"),
            ("branch_head_faculty_id",  "INTEGER"),
            ("academic_year_start",     "INTEGER")
        ]
        for name, decl in add_cols:
            if name not in have:
                try:
                    c.execute(f"ALTER TABLE subject_sessions ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_core ON subject_sessions(subject_id, session_date)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ay   ON subject_sessions(academic_year_start, session_date)")
        except sqlite3.OperationalError:
            pass

        # branches: ensure AY-scoped CIC & head fields exist, then indexes
        have_b = _table_cols(c, "branches")
        for name, decl in [
            ("branch_head_faculty_id", "INTEGER"),
            ("class_incharge_faculty_id", "INTEGER"),
            ("ay_start", "INTEGER"),
            ("year", "INTEGER"),
        ]:
            if name not in have_b:
                try:
                    c.execute(f"ALTER TABLE branches ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_branches_degree ON branches(degree_id)")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_branches_cic    ON branches(degree_id, ay_start, year)")
        except sqlite3.OperationalError:
            pass

        # notifications: ensure recipient column exists, then index
        have_n = _table_cols(c, "notifications")
        if "seen_by_faculty_id" not in have_n:
            try:
                c.execute("ALTER TABLE notifications ADD COLUMN seen_by_faculty_id INTEGER")
            except sqlite3.OperationalError:
                pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_notif_by_recipient ON notifications(seen_by_faculty_id, created_at)")
        except sqlite3.OperationalError:
            pass

        # users: ensure password_hash exists (older dbs had 'password')
        have_u = _table_cols(c, "users")
        if "password_hash" not in have_u:
            try:
                c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            except sqlite3.OperationalError:
                pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        except sqlite3.OperationalError:
            pass

        # subject_criteria: add commonly used columns first, then index
        have_sc = _table_cols(c, "subject_criteria")
        for name, decl in [
            ("batch_year", "INTEGER"),
            ("subject_in_charge_id", "INTEGER"),
            ("branch_id", "INTEGER"),
            ("lectures", "INTEGER DEFAULT 0"),
            ("studios", "INTEGER DEFAULT 0"),
            ("credits", "INTEGER DEFAULT 0"),
            ("internal_pct", "REAL DEFAULT 60.0"),
            ("external_pct", "REAL DEFAULT 40.0"),
            ("threshold_internal_pct", "REAL DEFAULT 50.0"),
            ("threshold_external_pct", "REAL DEFAULT 40.0"),
        ]:
            if name not in have_sc:
                try:
                    c.execute(f"ALTER TABLE subject_criteria ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_sc_dbs ON subject_criteria(degree_id, batch_year, semester)")
        except sqlite3.OperationalError:
            pass

        # theme_settings: add missing columns, then seed id=1 and backfill defaults
        theme_cols = _table_cols(c, "theme_settings")
        for name, decl in [
            ("theme_mode",  "TEXT"),
            ("font_family", "TEXT"),
            ("base_fg",     "TEXT"),
            ("base_bg",     "TEXT"),
            ("accent",      "TEXT"),
            ("pill_bg",     "TEXT"),
            ("pill_fg",     "TEXT"),
            ("button_bg",   "TEXT"),
            ("button_fg",   "TEXT"),
            ("header_bg",   "TEXT"),
            ("header_fg",   "TEXT"),
        ]:
            if name not in theme_cols:
                try:
                    c.execute(f"ALTER TABLE theme_settings ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass
        # Ensure single settings row, then sane defaults for NULLs
        c.execute("INSERT OR IGNORE INTO theme_settings(id) VALUES(1)")
        defaults = {
            "theme_mode":  "light",
            "font_family": "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Inter, Helvetica Neue, Arial, Noto Sans, sans-serif",
            "base_fg":     "#111111",
            "base_bg":     "#FFFFFF",
            "accent":      "#4F46E5",
            "pill_bg":     "#111111",
            "pill_fg":     "#FFFFFF",
            "button_bg":   "#111111",
            "button_fg":   "#FFFFFF",
            "header_bg":   "#111111",
            "header_fg":   "#FFFFFF",
        }
        for col, val in defaults.items():
            try:
                c.execute(f"UPDATE theme_settings SET {col}=COALESCE({col}, ?) WHERE id=1", (val,))
            except sqlite3.OperationalError:
                pass

        # ---------- Migrations for Subject Allocation layer ----------
        # subject_offerings: add columns if older DB existed via manual creation
        have_so = _table_cols(c, "subject_offerings")
        if have_so:
            for name, decl in [
                ("branch_id", "INTEGER"),
                ("subject_in_charge_id", "INTEGER"),
                ("academic_year_start", "INTEGER"),
            ]:
                if name not in have_so:
                    try:
                        c.execute(f"ALTER TABLE subject_offerings ADD COLUMN {name} {decl}")
                    except sqlite3.OperationalError:
                        pass
            try:
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_offering ON subject_offerings(subject_id, batch_year, semester, COALESCE(branch_id,-1))")
            except sqlite3.OperationalError:
                pass

        # subject_offering_faculty
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_sof ON subject_offering_faculty(offering_id, faculty_id, role)")
        except sqlite3.OperationalError:
            pass

        # subject_topic_offerings
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_sto_offering ON subject_topic_offerings(offering_id)")
        except sqlite3.OperationalError:
            pass

        # student_topic_choices
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_stc_choice ON student_topic_choices(offering_id, student_roll)")
        except sqlite3.OperationalError:
            pass


# ------------------------ Convenience (optional) --------------------

def reset_db_for_test() -> None:
    """Dangerous helper: drops everything. Use only in local testing."""
    with get_conn() as c:
        cur = c.cursor()
        for t in [
            "cic_change_log", "notifications", "subject_sessions", "assignments",
            "subject_topic_offerings", "student_topic_choices",
            "subject_offering_faculty", "subject_offerings",
            "subject_topics", "subject_faculty_map", "subject_criteria", "subjects",
            "holidays", "users", "faculty_roles", "faculty_degrees",
            "faculty", "branches", "degrees", "students", "branding", "theme_settings"
        ]:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass
    ensure_base_schema()


# Ensure schema + migrations on import (safe to run repeatedly)
ensure_base_schema()
run_light_migrations()
