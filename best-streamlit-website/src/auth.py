def check_credentials(username, password):
    # Predefined local user credentials
    valid_username = "user"
    valid_password = "password"

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