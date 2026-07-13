from demo_app.formatter import normalize_username


def test_normalize_username_trims_and_lowercases():
    assert normalize_username("  Alice  ") == "alice"


def test_normalize_username_preserves_inner_spacing():
    assert normalize_username("  Alice Smith  ") == "alice smith"
