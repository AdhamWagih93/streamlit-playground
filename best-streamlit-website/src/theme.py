import streamlit as st
import os

def set_theme(
    page_title: str = "Best Streamlit Website",
    page_icon: str = "🌐",
    layout: str = "wide",
    initial_sidebar_state: str = "expanded",
):
    """Configure Streamlit page & inject global CSS.

    Parameters allow per‑page override of title/icon without duplicating logic.
    Safe to call once at top of each page. Subsequent calls will be ignored by
    Streamlit for page_config but CSS will still be (re)injected.
    """
    try:
        st.set_page_config(
            page_title=page_title,
            page_icon=page_icon,
            layout=layout,
            initial_sidebar_state=initial_sidebar_state,
        )
    except Exception:
        # set_page_config can only be called once; ignore if already set.
        pass
    
    # Construct an absolute path to the CSS file
    # This makes it robust, no matter from where the script is run
    theme_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets', 'custom_theme.css')
    
    try:
        with open(theme_file, 'r', encoding='utf-8') as f:
            css = f.read()
            st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        st.error(f"Theme file not found at {theme_file}. Please check the file path.")