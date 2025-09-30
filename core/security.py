# core/security.py
from __future__ import annotations
import os
import random
import re
from typing import Optional, Dict, List, Tuple

from .db import get_conn, read_df

# ---------------------------
# users table (idempotent)
# ---------------------------
def _ensure_users_table():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT,
                status TEXT DEFAULT 'active',   -- 'active','new','pending','disabled'
                faculty_id INTEGER,
                is_active INTEGER DEFAULT 1
            )
        """)
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_faculty ON users(faculty_id)")
        except Exception:
            pass
        conn.commit()
def ensure_users_login_compat():
    """
    Make sure the users table has a plaintext 'password' column (your app authenticates on it),
    and reasonable defaults for status/is_active. Safe to call every run.
    """
    _ensure_users_table()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in cur.fetchall()}
        # Add missing columns (older DBs)
        if "password" not in cols:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN password TEXT")
            except Exception:
                pass
        if "status" not in cols:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
            except Exception:
                pass
        if "is_active" not in cols:
            try:
                cur.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")
            except Exception:
                pass
        conn.commit()

# ---------------------------
# Public helpers (kept for app.py)
# ---------------------------
def sanitize_username(u: str) -> str:
    u = (u or "").strip().lower()
    # keep alnum + dots/underscores only
    u = re.sub(r"[^a-z0-9._]+", "", u)
    return u[:64]

def create_user(*, username: str, password: str, role: str = "subject_faculty",
                status: str = "active", faculty_id: int | None = None, overwrite_password: bool = False):
    """Create or update a user. Plaintext password (to match app.py auth)."""
    _ensure_users_table()
    uname = sanitize_username(username)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (uname,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO users(username, password, role, status, faculty_id, is_active) VALUES(?,?,?,?,?,1)",
                (uname, password, role, status, faculty_id)
            )
        else:
            if overwrite_password:
                cur.execute(
                    "UPDATE users SET password=?, role=?, status=?, faculty_id=? WHERE id=?",
                    (password, role, status, faculty_id, row["id"])
                )
            else:
                cur.execute(
                    "UPDATE users SET role=?, status=?, faculty_id=? WHERE id=?",
                    (role, status, faculty_id, row["id"])
                )
        conn.commit()

def _reset_admin_user() -> tuple[bool, str]:
    """Create/Reset default 'admin' with password 'admin' as Superadmin."""
    try:
        create_user(username="admin", password="admin", role="superadmin",
                    status="active", overwrite_password=True)
        return True, "Default admin (admin / admin) is ready."
    except Exception as e:
        return False, f"Could not create/reset admin: {e}"

# ---------------------------
# Faculty → Users automation
# ---------------------------
_TITLES = re.compile(r"^(dr\.?|prof\.?|ar\.?|er\.?|architect|engineer|mr\.?|mrs\.?|ms\.?)\s+", re.I)

def _split_name(full: str) -> Tuple[str, str]:
    s = str(full or "").strip()
    s = re.sub(_TITLES, "", s).strip()
    parts = re.split(r"\s+", s)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0].lower(), ""
    return parts[0].lower(), parts[-1].lower()

def _base_username(first: str, last: str) -> str:
    if first and last:
        return (first[:5] + last[:1]).lower()
    return (first or last)[:6].lower()

def _ensure_unique_username(base: str) -> tuple[str, str]:
    """
    Return (username, suffix). Username is base + 4 digits, unique.
    """
    _ensure_users_table()
    with get_conn() as conn:
        cur = conn.cursor()
        for _ in range(2000):
            suffix = f"{random.randint(1000, 9999)}"
            cand = base + suffix
            cur.execute("SELECT 1 FROM users WHERE LOWER(username)=LOWER(?)", (cand,))
            if cur.fetchone() is None:
                return cand, suffix
    return base + str(random.randint(10000, 99999)), "9999"

def _temp_password(base: str, suffix: str) -> str:
    return f"{base}@{suffix}"

def create_user_for_faculty(faculty_id: int, faculty_name: str,
                            default_role: str = "subject_faculty") -> Optional[Dict]:
    """
    If this faculty has no linked user, create one and return credentials dict.
    """
    _ensure_users_table()
    # already linked?
    u = read_df("SELECT id FROM users WHERE faculty_id=?", (faculty_id,))
    if not u.empty:
        return None

    first, last = _split_name(faculty_name)
    base = _base_username(first, last)
    if not base:
        return None

    username, suffix = _ensure_unique_username(base)
    password = _temp_password(base, suffix)
    create_user(username=username, password=password, role=default_role,
                status="new", faculty_id=faculty_id, overwrite_password=False)
    return {"username": username, "temp_password": password, "role": default_role, "faculty_id": faculty_id}

def ensure_users_for_all_faculty(default_role: str = "subject_faculty") -> List[Dict]:
    """
    Go through faculty; if any row lacks a linked user, create one.
    Returns list of created credentials.
    """
    f = read_df("SELECT id, name FROM faculty ORDER BY name")
    created: List[Dict] = []
    if f.empty:
        return created
    linked = read_df("SELECT faculty_id FROM users WHERE faculty_id IS NOT NULL")
    linked_set = set([int(x) for x in linked["faculty_id"].dropna().tolist()]) if not linked.empty else set()
    for _, r in f.iterrows():
        fid = int(r["id"])
        if fid in linked_set:
            continue
        cred = create_user_for_faculty(fid, str(r["name"]), default_role=default_role)
        if cred:
            created.append(cred)
    return created
