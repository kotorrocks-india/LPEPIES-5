# seed_superadmin.py
from core.security import ensure_users_table, create_or_update_user

# make sure table/columns exist
ensure_users_table()

# create or overwrite a superadmin account (plaintext, since USE_HASHES=False)
username, stored = create_or_update_user(
    username="superadmin",
    password="super@123",
    role="superadmin",
    status="active",
    faculty_id=None,
    overwrite_password=True,   # set to False if you don’t want to overwrite
)

print(f"OK: username={username}, password=super@123 (stored={stored!r})")
