"""Shared pytest fixtures.

Sets test-only env vars *before* importing app/config, so Config's
fail-fast check (see config.py) doesn't need FLASK_DEBUG set just to run
the test suite, and DATABASE_URL points at a fresh temp SQLite file rather
than the real instance/kitstore.db - these tests create/delete their own
rows and should never touch real club data.
"""
import os
import re
import tempfile

import pytest

# Force (not setdefault) these, so the suite is hermetic regardless of
# whatever real values happen to be sitting in the ambient environment/.env.
os.environ["SECRET_KEY"] = "test-only-secret-key"
os.environ["COMMITTEE_PASSWORD"] = "test-only-password"
_db_fd, _db_path = tempfile.mkstemp(prefix="kitstore-test-", suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

import app as app_module  # noqa: E402 (must follow the env var setup above)


@pytest.fixture(scope="session")
def app():
    flask_app = app_module.app
    flask_app.config.update(TESTING=True)
    yield flask_app
    os.close(_db_fd)
    os.remove(_db_path)


@pytest.fixture()
def client(app):
    app_module._login_attempts.clear()
    return app.test_client()


def get_csrf_token(client, path):
    """Fetch a page and pull the csrf_field() token out of it, the way a
    real browser submitting that page's form would."""
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"no csrf token found on {path}"
    return match.group(1)


def login(client, committee_name="Test Committee"):
    """Log the given test client in as committee, the same way a real
    browser would (fetch the login page's token, submit the form)."""
    token = get_csrf_token(client, "/login")
    response = client.post(
        "/login",
        data={"password": "test-only-password", "committee_name": committee_name, "csrf_token": token},
        follow_redirects=True,
    )
    assert b"Logged in" in response.data
