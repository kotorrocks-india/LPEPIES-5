# app.py
from __future__ import annotations
import os
import sqlite3
import streamlit as st
import pandas as pd

from core.theme import render_theme_css
from core.branding import render_header, render_footer, get_login_background
from core.db import (
    get_conn,
    read_df,
    ensure_base_schema,
    run_light_migrations,   # <-- added
    exec_sql,               # optional, used by default admin creation
)
from core.security import _reset_admin_user, ensure_users_login_compat

# --- Screens (ALL from `screens/`) ---
from screens import (
    branding_page,
    appearance,
    degrees,
    faculty,  
    branches,
    students,
    users_passwords,
    pos,
    holidays,
    subject_criteria,
    subject_allocation,
    schedule,
    notifications,
    facultyinfo, 
)

st.set_page_config(page_title="EPLP/IES Manager", layout="wide")


# ----------------- DB hotfix: ensure new session columns exist -----------------
def _force_session_columns():
    """Guarantee new columns on subject_sessions for older DBs."""
    with get_conn() as conn:
        # Minimal shell (safe no-op if already exists)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subject_sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL,
                topic_id INTEGER,
                session_date TEXT NOT NULL
            )
        """)
        def _add(col, decl):
            try:
                conn.execute(f"ALTER TABLE subject_sessions ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # already present
        _add("slot",          "TEXT")                # 'morning'|'afternoon'|'both'
        _add("kind",          "TEXT")                # 'lecture'|'studio'|'both'
        _add("lectures",      "INTEGER DEFAULT 0")
        _add("studios",       "INTEGER DEFAULT 0")
        _add("lecture_notes", "TEXT")
        _add("studio_notes",  "TEXT")
        _add("assignment_id", "INTEGER")
        _add("due_date",      "TEXT")
        _add("completed",     "TEXT")


# ----------------- Auth helpers -----------------
def _ensure_default_admin():
    # keep your existing compatibility path
    ensure_users_login_compat()
    ok, msg = _reset_admin_user()
    if not ok:
        st.warning(msg)

def _get_user_row(username: str):
    try:
        return read_df(
            "SELECT id, username, password, role, status, faculty_id, is_active "
            "FROM users WHERE LOWER(username)=LOWER(?) LIMIT 1",
            (username.strip().lower(),)
        ).iloc[0].to_dict()
    except Exception:
        return None

def _login_user(username: str, password: str) -> dict | None:
    row = _get_user_row(username)
    if not row:
        return None
    if str(row.get("password","")) != str(password):
        return None
    if int(row.get("is_active",1)) == 0 or str(row.get("status","active")).lower() == "disabled":
        return None
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "role": str(row.get("role") or "subject_faculty").lower(),
        "faculty_id": row.get("faculty_id"),
        "status": row.get("status"),
    }


# --------------- Navigation ----------------
def _visible_pages_for(role: str) -> list[str]:
    r = (role or "").lower()
    if r == "superadmin":
        pages = [
            "Students","Faculty","Faculty Info","Holidays","Subject Criteria",
            "Program Outcomes (POs)","Degrees / Programs","Branches","Users & Passwords",
            "Appearance (Theme)","Branding","Schedule","Notifications","Subject Allocations"
        ]
    elif r in ("principal","director"):
        pages = [
            "Students","Faculty","Faculty Info","Holidays","Subject Criteria",
            "Program Outcomes (POs)","Degrees / Programs","Branches","Users & Passwords",
            "Appearance (Theme)","Schedule","Notifications"
        ]
    elif r == "class_in_charge":
        pages = ["Students","Faculty Info","Holidays","Subject Criteria","Program Outcomes (POs)","Schedule"]
    elif r in ("branch_head","subject_in_charge","subject_faculty"):
        pages = ["Faculty Info","Subject Criteria","Holidays","Students","Program Outcomes (POs)","Schedule"]
    else:
        pages = ["Students"]
    # de-dup but preserve order
    return list(dict.fromkeys(pages))

PAGES = {
    "Branding":                lambda u: branding_page.render(u),
    "Appearance (Theme)":      lambda u: appearance.render(u),
    "Degrees / Programs":      lambda u: degrees.render(u),
    "Faculty":                 lambda u: faculty.render(u),
    "Students":                lambda u: students.render(u),
    "Branches":                lambda u: branches.render(u),
    "Users & Passwords":       lambda u: users_passwords.render(u),
    "Program Outcomes (POs)":  lambda u: pos.render(u),
    "Holidays":                lambda u: holidays.render(u),
    "Subject Criteria":        lambda u: subject_criteria.render(u),
    "Subject Allocations":     lambda u: subject_allocation.render(u),
    "Schedule":                lambda u: schedule.render(u),
    "Notifications":           lambda u: notifications.render(u),
    "Faculty Info":            lambda u: facultyinfo.render(u),
}


# ----------------- Views -----------------
def _login_view():
    render_theme_css()

    # Optional full-page login background
    bg = get_login_background()
    if bg:
        st.markdown(
            f"""
            <style>
              .stApp {{
                background-image: url('{bg}');
                background-size: cover;
                background-position: center;
              }}
              .block-container {{
                background: rgba(255,255,255,0.88);
                border-radius: 12px;
                padding: 2rem;
              }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    _ensure_default_admin()

    st.markdown("### Sign in")
    with st.form("login_form"):
        u = st.text_input("Username", value="", autocomplete="username")
        p = st.text_input("Password", value="", type="password", autocomplete="current-password")
        ok = st.form_submit_button("Sign in", use_container_width=True)
    if ok:
        user = _login_user(u, p)
        if user:
            st.session_state["user"] = user
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.caption("Tip: first-time login → **admin / admin** (Superadmin).")


def _app_view(user: dict):
    render_theme_css()

    # reset header/footer guards so they're rendered once here
    st.session_state.pop("_hdr_done", None)
    st.session_state.pop("_ftr_done", None)

    with st.sidebar:
        st.markdown("### EPLP/IES Manager")
        st.write(f"**User:** {user.get('username','')}")
        st.write(f"**Role:** {user.get('role','')}")
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("user", None)
            st.rerun()

        st.markdown("---")
        st.caption("Navigate")

        pages = _visible_pages_for(user.get("role", ""))
        # Remember last page to avoid double-click issue
        prev = st.session_state.get("current_page", pages[0] if pages else "")
        if prev not in pages and pages:
            prev = pages[0]

        page_choice = st.selectbox(
            " ",
            pages,
            index=(pages.index(prev) if pages else 0),
            label_visibility="collapsed",
            key="__page_select__",
        )

        if page_choice != prev:
            st.session_state["current_page"] = page_choice
            st.rerun()

        st.session_state["current_page"] = page_choice

    render_header()
    if pages:
        PAGES[page_choice](user)
    else:
        st.info("No pages available for your role.")
    render_footer()


# ----------------- Main -----------------
def main():
    # Ensure DB schema + migrations + hotfix BEFORE any page queries
    ensure_base_schema()
    try:
        # If your project includes run_light_migrations() in core.db, call it:
        run_light_migrations()
    except Exception:
        pass
    _force_session_columns()

    # Touch connection early (and surface DB path if needed)
    try:
        with get_conn() as _:
            pass
    except Exception:
        pass

    user = st.session_state.get("user")
    if not user:
        _login_view()
    else:
        _app_view(user)


if __name__ == "__main__":
    pd.options.mode.copy_on_write = True
    main()
