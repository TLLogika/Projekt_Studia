"""Login screen; account verification is delegated to the authentication module."""

from typing import Protocol

import streamlit as st

from academic.errors import ApplicationError


class LoginService(Protocol):
    def login(self, login: str, password: str) -> str: ...


def _submit_login(auth: LoginService) -> None:
    """Run before the next page render so credential widget state can be cleared."""
    login = st.session_state.get("_login_username", "")
    password = st.session_state.get("_login_password", "")
    try:
        token = auth.login(login, password)
    except ApplicationError as error:
        st.session_state["_login_error"] = str(error)
    else:
        st.session_state["auth_token"] = token
        st.session_state.pop("_login_username", None)
        st.session_state.pop("_login_error", None)
    finally:
        st.session_state.pop("_login_password", None)


def render_login(auth: LoginService) -> None:
    """Authenticate an existing account without retaining its credentials."""
    _, content, _ = st.columns([1, 2, 1])
    with content:
        st.title("Logowanie")
        with st.form("login_form", clear_on_submit=True):
            st.text_input("Login", key="_login_username", autocomplete="username")
            st.text_input(
                "Hasło",
                type="password",
                key="_login_password",
                autocomplete="current-password",
            )
            st.form_submit_button(
                "Zaloguj się",
                type="primary",
                use_container_width=True,
                on_click=_submit_login,
                args=(auth,),
            )
        if st.session_state.get("auth_token"):
            st.rerun()
        if error := st.session_state.get("_login_error"):
            st.error(error)

