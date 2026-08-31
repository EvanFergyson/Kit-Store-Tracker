"""Regression tests for the CSRF protection added across every POST form.
Before this, any state-changing action (add/delete category, sign out kit,
approve/deny a request, ...) was a plain form with no token, so a logged-in
committee session visiting a malicious page could have it triggered
cross-site."""
from conftest import get_csrf_token, login

from models import Category, db


def test_post_without_csrf_token_is_rejected(client):
    login(client)
    response = client.post("/admin/categories/add", data={"name": "Should Not Be Created"})
    assert response.status_code == 400

    with client.application.app_context():
        assert Category.query.filter_by(name="Should Not Be Created").first() is None


def test_post_with_valid_csrf_token_is_accepted(client):
    login(client)
    token = get_csrf_token(client, "/admin/categories")
    response = client.post(
        "/admin/categories/add",
        data={"name": "CSRF Sanity Category", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Added category" in response.data

    with client.application.app_context():
        assert Category.query.filter_by(name="CSRF Sanity Category").first() is not None
        Category.query.filter_by(name="CSRF Sanity Category").delete()
        db.session.commit()


def test_stale_or_forged_token_is_rejected(client):
    login(client)
    response = client.post(
        "/admin/categories/add",
        data={"name": "Should Also Not Be Created", "csrf_token": "not-a-real-token"},
    )
    assert response.status_code == 400

    with client.application.app_context():
        assert Category.query.filter_by(name="Should Also Not Be Created").first() is None
