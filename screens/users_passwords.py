# pages/users_passwords.py
from __future__ import annotations
import re
import pandas as pd
import streamlit as st

from core.db import read_df, get_conn
from core.theme import render_theme_css
from core.branding import render_header, render_footer
from core.security import (
    create_user,             # create/update (plaintext auth), supports overwrite_password
    sanitize_username,       # normalize usernames
)

# ---------------- Permissions ----------------
def can_manage(role: str) -> bool:
    return (role or "").lower() in ("superadmin", "principal", "director")

# ---------------- Schema guard ----------------
def _ensure_users():
    # Match security.py users table (add is_active and status default)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT,
                status TEXT DEFAULT 'active',
                faculty_id INTEGER,
                is_active INTEGER DEFAULT 1
            )
        """)
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_faculty ON users(faculty_id)")
        except Exception:
            pass
        conn.commit()

# ------------- Suggest credentials (local) -------------
# Mirrors the logic used by core.security: strip titles, make base, add 4 digits, password base@suffix
_TITLES = re.compile(r"^(dr\.?|prof\.?|ar\.?|er\.?|architect|engineer|mr\.?|mrs\.?|ms\.?)\s+", re.I)

def _strip_title(name: str) -> str:
    return re.sub(_TITLES, "", str(name or "").strip()).strip()

def _split_name(full: str) -> tuple[str, str]:
    s = _strip_title(full)
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

def suggest_credentials(full_name: str) -> tuple[str, str]:
    first, last = _split_name(full_name)
    base = _base_username(first, last) or "user"
    # Show a sample; the actual unique suffix will be assigned on create/update
    sample_suffix = "1234"
    return sanitize_username(base + sample_suffix), f"{base}@{sample_suffix}"

# ---------------- Page ----------------
def render(user: dict):
    _ensure_users()
    render_theme_css()
    render_header()
    st.header("Users & Passwords")

    role = (user.get("role") or "").lower()
    editable = can_manage(role)
    if not editable:
        st.info("Only Superadmin / Principal / Director can manage users.")

    # List users
    df = read_df("""
        SELECT id, username, COALESCE(role,'') AS role,
               COALESCE(status,'active') AS status,
               COALESCE(faculty_id,'') AS faculty_id,
               COALESCE(is_active,1) AS is_active
        FROM users ORDER BY username
    """)
    st.subheader("All users")
    st.dataframe(
        df if not df.empty else pd.DataFrame(columns=["id","username","role","status","faculty_id","is_active"]),
        use_container_width=True
    )

    # Backfill accounts for any faculty who don't have a user yet
    from core.security import ensure_users_for_all_faculty
    st.divider()
    st.subheader("Provision missing faculty accounts")
    if st.button("Generate for all missing faculty", disabled=not editable):
        created = ensure_users_for_all_faculty(default_role="subject_faculty")
        if created:
            st.success(f"Created {len(created)} account(s).")
            with st.expander("New credentials (one-time)"):
                st.dataframe(pd.DataFrame(created), use_container_width=True)
        else:
            st.info("No missing accounts found. All faculty already have users.")

    with st.expander("Faculty without users"):
        missing = read_df("""
            SELECT f.id, f.name, f.type, f.email
            FROM faculty f
            LEFT JOIN users u ON u.faculty_id = f.id
            WHERE u.id IS NULL
            ORDER BY f.name
        """)
        st.dataframe(
            missing if not missing.empty else pd.DataFrame({"info":["All faculty have users."]}),
            use_container_width=True
        )

    st.divider()
    st.subheader("Create / Update User")
    with st.form("user_edit", clear_on_submit=True):
        username_in = st.text_input("Username").strip().lower()
        password_in = st.text_input("Password", type="password")
        role_pick   = st.selectbox("Role", [
            "superadmin","principal","director","branch_head",
            "class_in_charge","subject_in_charge","subject_faculty"
        ])
        status_in   = st.selectbox("Status", ["active","new","pending","disabled"], index=0)
        faculty_id_in = st.text_input("Faculty ID (optional)").strip()
        overwrite_pwd = st.checkbox("Overwrite existing password if user exists", value=False)
        ok_save = st.form_submit_button("Save", disabled=not editable)

    if ok_save and editable:
        if not username_in or not password_in:
            st.warning("Username and password are required.")
        else:
            try:
                uname = sanitize_username(username_in)
                fid = int(faculty_id_in) if faculty_id_in.isdigit() else None
                # Use create_user with overwrite toggled as needed
                create_user(
                    username=uname,
                    password=password_in,
                    role=role_pick,
                    status=status_in,
                    faculty_id=fid,
                    overwrite_password=overwrite_pwd
                )
                st.success(f"User saved: {uname}")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

    st.subheader("Reset Password")
    with st.form("user_reset", clear_on_submit=True):
        uname_reset = st.text_input("Username to reset").strip().lower()
        newpw_reset = st.text_input("New password", type="password")
        ok_reset = st.form_submit_button("Reset", disabled=not editable)

    if ok_reset and editable:
        if not uname_reset or not newpw_reset:
            st.warning("Username and new password are required.")
        else:
            try:
                uname = sanitize_username(uname_reset)
                # Reset = update with overwrite_password=True (role/status/faculty_id unchanged if we pass None)
                # Fetch current to preserve role/status/faculty_id
                cur = read_df("SELECT role, status, faculty_id FROM users WHERE LOWER(username)=LOWER(?) LIMIT 1", (uname,))
                if cur.empty:
                    st.error("User not found.")
                else:
                    role0   = str(cur.iloc[0]["role"] or "subject_faculty")
                    status0 = str(cur.iloc[0]["status"] or "active")
                    fid0    = int(cur.iloc[0]["faculty_id"]) if str(cur.iloc[0]["faculty_id"]).strip().isdigit() else None
                    create_user(
                        username=uname,
                        password=newpw_reset,
                        role=role0,
                        status=status0,
                        faculty_id=fid0,
                        overwrite_password=True
                    )
                    st.success("Password reset.")
                    st.rerun()
            except Exception as e:
                st.error(f"Reset failed: {e}")

    st.divider()
    st.subheader("Suggest Credentials From Name")
    nm = st.text_input("Full name (e.g., Ar. Parikshit Waghdhare)")
    if st.button("Suggest"):
        if nm.strip():
            u, p = suggest_credentials(nm)
            st.write(f"**Username (example):** `{u}`  •  **Password (example):** `{p}`")
            st.caption("Note: the final username will include a unique 4-digit suffix when the account is created.")
        else:
            st.warning("Enter a name first.")

    render_footer()
