"""The restrained, dark visual treatment specified in sources/UI.jpg."""

import streamlit as st


def apply_styles() -> None:
    """Apply shared styles without changing Streamlit widget behaviour."""
    st.markdown(
        """
        <style>
        .stApp {
            background: #111111;
            color: #ededed;
        }
        [data-testid="stHeader"] {
            background: #111111;
        }
        [data-testid="stSidebar"] {
            background: #111111;
            border-right: 1px solid #777777;
        }
        [data-testid="stSidebar"] [data-testid="stButton"] button {
            border-radius: 0;
            text-align: left;
            justify-content: flex-start;
        }
        .block-container {
            max-width: 1120px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }
        [data-testid="stExpander"],
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: #777777 !important;
            border-radius: 0 !important;
        }
        [data-testid="stExpander"] details {
            background: #111111;
        }
        [data-testid="stExpander"] summary {
            padding: 1rem;
        }
        [data-testid="stButton"] button,
        [data-testid="stFormSubmitButton"] button {
            border: 1px solid #777777;
            border-radius: 0;
            min-height: 2.75rem;
        }
        [data-testid="stButton"] button[kind^="primary"],
        [data-testid="stFormSubmitButton"] button[kind^="primary"] {
            background: #ededed;
            color: #111111;
            border-color: #ededed;
        }
        [data-testid="stButton"] button[kind^="primary"]:hover,
        [data-testid="stFormSubmitButton"] button[kind^="primary"]:hover {
            background: #ffffff;
            color: #111111;
        }
        [data-testid="stButton"] button[kind^="primary"] p,
        [data-testid="stButton"] button[kind^="primary"] span,
        [data-testid="stFormSubmitButton"] button[kind^="primary"] p,
        [data-testid="stFormSubmitButton"] button[kind^="primary"] span {
            color: #111111 !important;
        }
        button[data-testid^="stBaseButton-primary"],
        button[data-testid^="stBaseButton-primary"] p,
        button[data-testid^="stBaseButton-primary"] span {
            color: #111111 !important;
        }
        [data-testid="stButton"] button:disabled {
            color: #999999;
            border-color: #3e3e3e;
        }
        [data-testid="stTextInput"] input:disabled {
            -webkit-text-fill-color: #c7c7c7;
            color: #c7c7c7;
        }
        h1, h2, h3 {
            font-weight: 500;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
