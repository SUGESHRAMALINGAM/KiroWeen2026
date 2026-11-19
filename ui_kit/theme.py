"""Zombie theme for Streamlit. Plain Mode skips all of it."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

CSS_PATH = Path(__file__).with_name("theme.css")


def theme_css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def inject_theme(plain: bool) -> None:
    """Inject the zombie CSS unless Plain Mode is on."""
    if not plain:
        st.markdown(f"<style>{theme_css()}</style>", unsafe_allow_html=True)
