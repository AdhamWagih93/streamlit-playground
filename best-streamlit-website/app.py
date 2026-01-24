import streamlit as st
from src.theme import set_theme
from src.admin_config import load_admin_config
from src.page_catalog import catalog_by_group, known_page_paths
from src.settings_ui import render_global_settings

set_theme(page_title="Best Streamlit Website", page_icon="🌐")

admin = load_admin_config(known_pages=known_page_paths())

# Global settings launcher (cog in top-right) + Settings dialog.
# This is intentionally not part of the navigation pages.
render_global_settings()

# Official Streamlit navigation API with grouped sections, filtered by admin config.
pages = {}
for group, specs in catalog_by_group().items():
    items = []
    for spec in specs:
        enabled = admin.is_page_enabled(spec.path, default=True)
        if not enabled and not spec.always_visible:
            continue
        items.append(st.Page(spec.path, title=spec.title, icon=spec.icon))
    if items:
        pages[group] = items

pg = st.navigation(pages, position="top")
pg.run()
