import os

def check_credentials(username, password):
    import os
    valid_password = os.environ.get("STREAMLIT_APP_PASSWORD", "ChangeThisStrongPassword123!")
    valid_username = os.environ.get("VALID_USERNAME", "")
    valid_password = os.environ.get("VALID_PASSWORD", "")

    return username == valid_username and password == valid_password

def login(username, password):
    if check_credentials(username, password):
        return True
    else:
        return False

def logout():
    # Logic for logging out the user
    pass

def is_logged_in(session_state):
    return session_state.get('logged_in', False)

def set_login_state(session_state, state):
    session_state['logged_in'] = state