from src import theme

def test_set_theme():
    # Example: set a theme and check for side effects or exceptions
    try:
        theme.set_theme(primaryColor="#FF0000")
    except Exception as e:
        assert False, f"set_theme raised an exception: {e}"
