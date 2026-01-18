def test_app_imports_successfully():
    from app.main import app

    assert app.title == "AI Presale MVP"
