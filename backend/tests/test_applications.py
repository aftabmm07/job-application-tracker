from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app, get_db
from app.models import Base, User
from app.auth_config import AuthSettings, get_auth_settings
from app.auth_limits import AuthRateLimiter, get_auth_limiter
from app.passwords import hash_password


@pytest.fixture(scope="module")
def application_password_hash():
    return hash_password("application regression test password")


@pytest.fixture
def client(application_password_hash):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with TestingSessionLocal() as db:
        db.add(User(email="regression@example.com", password_hash=application_password_hash,
                    is_active=True, email_verified_at=datetime.now(UTC)))
        db.commit()

    settings = AuthSettings.from_environment({})
    limiter = AuthRateLimiter()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_settings] = lambda: settings
    app.dependency_overrides[get_auth_limiter] = lambda: limiter

    try:
        with TestClient(app) as test_client:
            origin = {"Origin": "http://localhost:5173"}
            csrf = test_client.get("/auth/csrf", headers=origin).json()["csrf_token"]
            login = test_client.post("/auth/login", headers=origin | {"X-CSRF-Token": csrf},
                                    json={"email": "regression@example.com",
                                          "password": "application regression test password"})
            assert login.status_code == 200
            csrf = test_client.get("/auth/csrf", headers=origin).json()["csrf_token"]
            test_client.headers.update(origin | {"X-CSRF-Token": csrf, "Content-Type": "application/json"})
            yield test_client
    finally:
        for dependency in (get_db, get_auth_settings, get_auth_limiter):
            app.dependency_overrides.pop(dependency, None)
        engine.dispose()


def test_create_application(client):
    payload = {
        "company_name": "Example Tech GmbH",
        "job_title": "Working Student DevOps",
        "location": "Dortmund, Germany",
        "status": "saved",
    }

    response = client.post("/applications", json=payload)

    assert response.status_code == 201

    data = response.json()

    assert data["id"] == 1
    assert data["company_name"] == "Example Tech GmbH"
    assert data["job_title"] == "Working Student DevOps"
    assert data["location"] == "Dortmund, Germany"
    assert data["status"] == "saved"
    assert data["created_at"] is not None


def test_list_applications(client):
    client.post(
        "/applications",
        json={
            "company_name": "Microsoft",
            "job_title": "Cloud Engineer",
        },
    )

    client.post(
        "/applications",
        json={
            "company_name": "Example Tech GmbH",
            "job_title": "DevOps Engineer",
        },
    )

    response = client.get("/applications")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 2
    assert data[0]["company_name"] == "Example Tech GmbH"
    assert data[1]["company_name"] == "Microsoft"


def test_get_application_by_id(client):
    create_response = client.post(
        "/applications",
        json={
            "company_name": "SAP",
            "job_title": "Working Student Software Engineering",
        },
    )

    application_id = create_response.json()["id"]

    response = client.get(f"/applications/{application_id}")

    assert response.status_code == 200
    assert response.json()["id"] == application_id
    assert response.json()["company_name"] == "SAP"

    missing_response = client.get("/applications/9999")

    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Job application not found"


def test_update_application(client):
    create_response = client.post(
        "/applications",
        json={
            "company_name": "Microsoft",
            "job_title": "Cloud Engineer",
            "status": "saved",
        },
    )

    application_id = create_response.json()["id"]

    response = client.patch(
        f"/applications/{application_id}",
        json={
            "status": "interview",
            "location": "Berlin, Germany",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "interview"
    assert data["location"] == "Berlin, Germany"
    assert data["company_name"] == "Microsoft"
    assert data["job_title"] == "Cloud Engineer"

    missing_response = client.patch(
        "/applications/9999",
        json={"status": "rejected"},
    )

    assert missing_response.status_code == 404


def test_delete_application(client):
    create_response = client.post(
        "/applications",
        json={
            "company_name": "Example GmbH",
            "job_title": "DevOps Working Student",
        },
    )

    application_id = create_response.json()["id"]

    delete_response = client.delete(
        f"/applications/{application_id}"
    )

    assert delete_response.status_code == 204

    get_response = client.get(
        f"/applications/{application_id}"
    )

    assert get_response.status_code == 404

    missing_response = client.delete("/applications/9999")

    assert missing_response.status_code == 404


def test_create_application_invalid_input(client):
    invalid_payloads = [
        {
            "company_name": "",
            "job_title": "DevOps Engineer",
        },
        {
            "company_name": "Example GmbH",
            "job_title": "Cloud Engineer",
            "status": "unknown",
        },
        {
            "company_name": "A" * 151,
            "job_title": "Software Engineer",
        },
    ]

    for payload in invalid_payloads:
        response = client.post(
            "/applications",
            json=payload,
        )

        assert response.status_code == 422

    # Invalid requests must not create database records.
    response = client.get("/applications")

    assert response.status_code == 200
    assert response.json() == []


def test_update_application_invalid_null(client):
    create_response = client.post(
        "/applications",
        json={
            "company_name": "Example GmbH",
            "job_title": "DevOps Engineer",
        },
    )

    application_id = create_response.json()["id"]

    for field in ["company_name", "job_title", "status"]:
        response = client.patch(
            f"/applications/{application_id}",
            json={field: None},
        )

        assert response.status_code == 422

    # Verify that the original application remains unchanged.
    response = client.get(f"/applications/{application_id}")

    assert response.status_code == 200
    assert response.json()["company_name"] == "Example GmbH"
    assert response.json()["job_title"] == "DevOps Engineer"
    assert response.json()["status"] == "saved"


def test_reject_whitespace_only_required_fields(client):
    invalid_payloads = [
        {
            "company_name": "   ",
            "job_title": "DevOps Engineer",
        },
        {
            "company_name": "Example GmbH",
            "job_title": "   ",
        },
    ]

    for payload in invalid_payloads:
        response = client.post(
            "/applications",
            json=payload,
        )

        assert response.status_code == 422

    # Verify that no invalid applications were saved.
    response = client.get("/applications")

    assert response.status_code == 200
    assert response.json() == []
