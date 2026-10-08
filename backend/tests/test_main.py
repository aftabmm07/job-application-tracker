
from fastapi.testclient import TestClient

from app.main import app
from unittest.mock import patch
from sqlalchemy.exc import OperationalError


client = TestClient(app)


def test_home():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to Job Application Tracker API",
        "status": "running"
    }


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy"
    }


def test_database_health_success():
    with patch("app.main.engine.connect") as mock_connect:
        response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "database": "connected",
    }
    mock_connect.assert_called_once()


def test_database_health_failure():
    with patch("app.main.engine.connect") as mock_connect:
        mock_connect.side_effect = OperationalError(
            "SELECT 1",
            {},
            Exception("Database connection failed"),
        )

        response = client.get("/health/db")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Database unavailable",
    }
