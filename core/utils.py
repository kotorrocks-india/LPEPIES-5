# core/utils.py
from __future__ import annotations
import io
import re
import unicodedata
import pandas as pd

# ---------------- CSV / bytes helpers ----------------

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None or df.empty:
        df = pd.DataFrame()
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")

# ---------------- name / title helpers ----------------

_TITLE_PATTERNS = [
    r"\bprof(?:essor)?\.?", r"\bdr\.?", r"\bar(?:ch(?:itect)?)?\.?", r"\ber(?:ngineer)?\.?",
    r"\bmr\.?", r"\bms\.?", r"\bmrs\.?", r"\bshri\b", r"\bsmt\b"
]
_TITLE_REGEX = re.compile(r"^(?:" + r"|".join(_TITLE_PATTERNS) + r")\s+", re.IGNORECASE)

def normalize_whitespace(s: str) -> str:
    return " ".join(str(s or "").split())

def split_title_name(raw: str) -> tuple[str, str]:
    """
    Splits leading title (Dr/Prof/Ar/Er/Mr/Ms/Mrs, etc.) from a name.
    Returns (title, clean_name). Title may be ''.
    """
    s = normalize_whitespace(raw)
    # Make sure we process in NFKC to normalize dots/spacing
    s = unicodedata.normalize("NFKC", s)
    m = _TITLE_REGEX.match(s)
    if not m:
        return ("", s)
    title = s[: m.end()].strip().rstrip(".")
    clean = s[m.end():].strip()
    return (title, clean)

# ---------------- academic year / batch helpers ----------------

def parse_join_year_from_roll(roll: str | int | None) -> int | None:
    """
    Extract the first 4 consecutive digits from the start of the roll number,
    and treat it as the joining year (e.g., '2022ABC...' -> 2022).
    Returns None if not plausible (outside 1900..2100).
    """
    if roll is None:
        return None
    s = str(roll).strip()
    m = re.match(r"^\s*(\d{4})", s)
    if not m:
        return None
    y = int(m.group(1))
    if 1900 <= y <= 2100:
        return y
    return None

def academic_program_year(join_year: int | None, today=None, duration_years: int = 5) -> int | None:
    """
    Academic year boundary assumed at June.
    If joined in June YYYY, then:
      - Jun–Dec YYYY => Year 1
      - Jan–May YYYY+1 => still Year 1
      - Jun YYYY+1 => Year 2, etc.
    Returns clamped 1..duration_years, or None if join_year unknown.
    """
    from datetime import date
    if join_year is None:
        return None
    if today is None:
        today = date.today()
    years = today.year - int(join_year)
    y = years + 1 if today.month >= 6 else years
    y = max(1, min(int(duration_years or 5), y))
    return y

def batch_label(join_year: int | None, duration_years: int = 5) -> str:
    """
    Builds 'YYYY–YYYY+duration' label, e.g., '2022–2027' for duration 5.
    Returns '' if join_year unknown.
    """
    if join_year is None:
        return ""
    try:
        jy = int(join_year)
        return f"{jy}–{jy + int(duration_years or 5)}"
    except Exception:
        return ""
