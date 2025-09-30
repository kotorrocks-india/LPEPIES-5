# screens/appearance.py
from __future__ import annotations
import streamlit as st
from core.theme import render_theme_css, set_theme
from core.branding import render_header, render_footer

FONT_OPTIONS = {
    "Inter (sans)": "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif",
    "Segoe UI (sans)": "'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif",
    "Roboto (sans)": "Roboto, -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif",
    "Nunito (rounded sans)": "Nunito, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif",
    "Poppins (sans)": "Poppins, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif",
    "Source Sans Pro (sans)": "'Source Sans Pro', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif",
    "Merriweather (serif)": "Merriweather, Georgia, 'Times New Roman', serif",
    "Georgia (serif)": "Georgia, 'Times New Roman', serif",
    "Mono (code)": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
}

def _font_css_link(name: str) -> str:
    m = {
        "Inter (sans)": "Inter:wght@400;600",
        "Nunito (rounded sans)": "Nunito:wght@400;600",
        "Poppins (sans)": "Poppins:wght@400;600",
        "Source Sans Pro (sans)": "Source+Sans+3:wght@400;600",
        "Merriweather (serif)": "Merriweather:wght@400;700",
    }
    fam = m.get(name)
    return f"<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family={fam}&display=swap'>" if fam else ""

def render(user: dict):
    if not user or not user.get("username"):
        st.warning("Please sign in to continue.")
        st.stop()

    render_theme_css()
    render_header()
    st.header("Appearance (Theme)")

    # Make inputs readable regardless of palette
    st.markdown("""
    <style>
      label, .stMarkdown p, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        color: var(--app-text) !important;
      }
      .stTextInput input, .stNumberInput input, .stTextArea textarea,
      [data-baseweb="select"] div[role="button"] {
        background: #ffffff !important;
        color: var(--app-text) !important;
        border: 1px solid rgba(0,0,0,0.18) !important;
        border-radius: var(--app-radius) !important;
      }
      input[type="color"] { border:1px solid rgba(0,0,0,0.2); height:42px; width:42px; padding:0; border-radius:8px; }
      code, .stMarkdown code { background:transparent !important; color:inherit !important; padding:0 !important; }
      .palette {display:flex; gap:8px; margin:8px 0 16px;}
      .swatch {flex:1; height:36px; border-radius:8px; border:1px solid rgba(0,0,0,0.08);}
    </style>
    """, unsafe_allow_html=True)

    st.markdown("Use this page to apply a **readable, high-contrast** theme or tweak colors & fonts.")

    # Quick reset
    with st.expander("⚡ Quick Reset (recommended on first run)", expanded=True):
        if st.button("Reset to high-contrast defaults", type="primary"):
            set_theme(
                base="light",
                primary_color="#2563eb",
                accent_color="#f59e0b",
                bg="#ffffff",                 # PAGE BACKGROUND
                text_color="#0f172a",
                card_bg="#ffffff",            # CARD/FORM BACKGROUND
                sidebar_bg="#f1f5f9",
                sidebar_text="#0f172a",
                header_bg="#ffffff",
                header_text="#0f172a",
                radius="12px",
                font_family=FONT_OPTIONS["Inter (sans)"],
            )
            st.success("Theme updated.")
            st.rerun()

    st.divider()
    st.subheader("Quick Tweaks")

    # Colors (now includes Page background & Card background)
    c1, c2, c3 = st.columns(3)
    with c1:
        primary     = st.color_picker("Primary color", value="#2563eb", help="Buttons & highlights")
        header_bg   = st.color_picker("Header background", value="#ffffff")
    with c2:
        accent      = st.color_picker("Accent color", value="#f59e0b", help="Emphasis elements")
        sidebar_bg  = st.color_picker("Sidebar background", value="#f1f5f9")
    with c3:
        text_color  = st.color_picker("Text color", value="#0f172a")
        radius      = st.text_input("Corner radius", value="12px")

    # NEW: page & card backgrounds
    b1, b2 = st.columns(2)
    with b1:
        page_bg = st.color_picker("Page background (canvas)", value="#ffffff")
    with b2:
        card_bg = st.color_picker("Card/Form background", value="#ffffff")

    # Palette preview
    st.markdown(
        f"""
        <div class="palette">
          <div class="swatch" style="background:{page_bg}" title="Page background"></div>
          <div class="swatch" style="background:{card_bg}" title="Card background"></div>
          <div class="swatch" style="background:{primary}" title="Primary"></div>
          <div class="swatch" style="background:{accent}" title="Accent"></div>
          <div class="swatch" style="background:{text_color}" title="Text"></div>
          <div class="swatch" style="background:{header_bg}" title="Header bg"></div>
          <div class="swatch" style="background:{sidebar_bg}" title="Sidebar bg"></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Fonts with live preview
    st.subheader("Typography")
    font_name = st.selectbox("Font family", list(FONT_OPTIONS.keys()), index=0)
    font_value = FONT_OPTIONS[font_name]
    st.markdown(_font_css_link(font_name), unsafe_allow_html=True)
    st.markdown(
        f"""
        <div style="font-family:{font_value}; border:1px solid rgba(0,0,0,0.08); padding:12px 14px; border-radius:12px; background:#fff;">
          <div style="font-size:1.35rem; font-weight:600; margin-bottom:6px;">The quick brown fox</div>
          <div style="opacity:0.85;">jumps over the lazy dog — 1234567890 ! @ #</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    if st.button("Save tweaks"):
        set_theme(
            primary_color=primary,
            accent_color=accent,
            bg=page_bg,          # apply page background
            card_bg=card_bg,     # apply card background
            text_color=text_color,
            header_bg=header_bg,
            sidebar_bg=sidebar_bg,
            radius=radius,
            font_family=font_value,
        )
        st.success("Saved. Reloading theme…")
        st.rerun()

    render_footer()
