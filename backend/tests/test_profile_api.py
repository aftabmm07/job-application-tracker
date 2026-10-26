"""Private profile API checks against isolated SQLite sessions only."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.dependencies import get_db
from app.main import app
from app.models import Base, User
from app.passwords import hash_password

ORIGIN = "http://localhost:5173"
PASSWORD = "profile test password with spaces"
PROFILE_FIELDS = {"email", "full_name", "professional_headline", "location",
                  "linkedin_url", "github_url", "portfolio_url"}


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def ctx(password_hash):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = AuthSettings.from_environment({"VERIFICATION_FRONTEND_ORIGIN": ORIGIN})
    with Session(engine) as db:
        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_auth_settings] = lambda: settings
        app.dependency_overrides[get_auth_limiter] = lambda: AuthRateLimiter()
        try:
            with TestClient(app) as client:
                yield SimpleNamespace(db=db, client=client, password_hash=password_hash, csrf=None)
        finally:
            for dependency in (get_db, get_auth_settings, get_auth_limiter):
                app.dependency_overrides.pop(dependency, None)
    engine.dispose()


def add_user(ctx, email):
    user = User(email=email, password_hash=ctx.password_hash,
                is_active=True, email_verified_at=datetime.now(UTC))
    ctx.db.add(user)
    ctx.db.commit()
    return user.id


def csrf(ctx):
    response = ctx.client.get("/auth/csrf", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    ctx.csrf = response.json()["csrf_token"]


def sign_in(ctx, email):
    csrf(ctx)
    response = ctx.client.post("/auth/login", json={"email": email, "password": PASSWORD},
                               headers={"Origin": ORIGIN, "X-CSRF-Token": ctx.csrf})
    assert response.status_code == 200
    csrf(ctx)


def patch_profile(ctx, data, **headers):
    return ctx.client.patch(
        "/auth/profile", json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": ctx.csrf} | headers,
    )


def test_profile_requires_verified_authenticated_session(ctx):
    assert ctx.client.get("/auth/profile").status_code == 401
    csrf(ctx)
    assert patch_profile(ctx, {"full_name": "Nobody"}).status_code == 401


def test_profile_defaults_and_read_only_email(ctx):
    add_user(ctx, "first@example.com")
    sign_in(ctx, "first@example.com")
    response = ctx.client.get("/auth/profile")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == PROFILE_FIELDS
    assert response.json() == {"email": "first@example.com"} | {
        name: None for name in PROFILE_FIELDS - {"email"}}


def test_partial_update_blank_clearing_and_owner_isolation(ctx):
    first_id = add_user(ctx, "first@example.com")
    second_id = add_user(ctx, "second@example.com")
    sign_in(ctx, "first@example.com")
    response = patch_profile(ctx, {
        "full_name": "  Ada Lovelace  ",
        "professional_headline": "Engineer",
        "location": " Berlin ",
        "github_url": "https://github.com/example",
    })
    assert response.status_code == 200
    assert response.json()["full_name"] == "Ada Lovelace"
    assert response.json()["location"] == "Berlin"
    assert response.json()["github_url"] == "https://github.com/example"
    assert response.json()["email"] == "first@example.com"
    assert ctx.db.get(User, second_id).full_name is None
    assert patch_profile(ctx, {"location": "  "}).json()["location"] is None
    assert ctx.db.get(User, first_id).professional_headline == "Engineer"
    assert patch_profile(ctx, {}).status_code == 200
    # A different authenticated account sees only its own defaults.
    csrf(ctx)
    assert ctx.client.post("/auth/logout", json={}, headers={"Origin": ORIGIN,
        "X-CSRF-Token": ctx.csrf}).status_code == 204
    sign_in(ctx, "second@example.com")
    assert ctx.client.get("/auth/profile").json()["full_name"] is None


@pytest.mark.parametrize("field", ["email", "id", "is_active", "email_verified_at",
                                   "password_hash", "user_id"])
def test_profile_rejects_forbidden_fields(ctx, field):
    user_id = add_user(ctx, "first@example.com")
    sign_in(ctx, "first@example.com")
    response = patch_profile(ctx, {field: "changed"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid authentication request"}
    assert ctx.db.get(User, user_id).email == "first@example.com"


@pytest.mark.parametrize("data", [
    {"full_name": "x" * 121}, {"professional_headline": "x" * 161},
    {"location": "x" * 121}, {"portfolio_url": "https://example.com/" + "x" * 500},
    {"linkedin_url": "http://example.com"},
    {"github_url": "javascript:alert(1)"},
    {"portfolio_url": "https://user:pass@example.com"},
    {"github_url": "https://127.0.0.1/account"},
    {"portfolio_url": "https://localhost/account"},
])
def test_profile_rejects_invalid_values(ctx, data):
    add_user(ctx, "first@example.com")
    sign_in(ctx, "first@example.com")
    response = patch_profile(ctx, data)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid authentication request"}


def test_profile_mutation_requires_csrf_and_trusted_origin(ctx):
    user_id = add_user(ctx, "first@example.com")
    sign_in(ctx, "first@example.com")
    assert patch_profile(ctx, {"full_name": "Rejected"}, **{"X-CSRF-Token": "invalid"}).status_code == 403
    assert patch_profile(ctx, {"full_name": "Rejected"}, Origin="http://evil.example").status_code == 403
    assert ctx.db.get(User, user_id).full_name is None


def test_profile_commit_failure_rolls_back_and_returns_sanitized_error(ctx):
    user_id = add_user(ctx, "first@example.com")
    sign_in(ctx, "first@example.com")
    with patch.object(ctx.db, "commit", side_effect=SQLAlchemyError("synthetic failure")):
        response = patch_profile(ctx, {"full_name": "Uncommitted"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable"}
    assert "synthetic" not in response.text
    ctx.db.expire_all()
    assert ctx.db.get(User, user_id).full_name is None
