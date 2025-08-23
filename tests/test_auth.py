import pytest
from src import auth

def test_login_success():
    assert auth.login('user', 'password') == True

def test_login_failure():
    assert auth.login('user', 'wrongpass') == False
