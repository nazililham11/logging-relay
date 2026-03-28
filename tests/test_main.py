import os
import secrets
import pytest
from fastapi.testclient import TestClient
from main import app

# Ambil konfigurasi dari environment
MASTER_ADMIN_KEY = os.getenv("MASTER_ADMIN_KEY")
TEST_PROJECT_NAME = os.getenv("TEST_PROJECT_NAME", "logging-relay-test")
TEST_PROJECT_TOKEN = os.getenv("TEST_PROJECT_TOKEN", "logging-relay-test-token")


@pytest.fixture
def client():
    """Fixture untuk TestClient dengan lifespan."""
    with TestClient(app) as c:
        yield c


def test_health_real(client):
    """Test health endpoint secara langsung."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["redis"] == "connected"
    assert data["loki"] == "connected"


def test_project_lifecycle_and_logging_real(client):
    """Test alur registrasi project, pengiriman log, dan penghapusan project."""
    project_name = TEST_PROJECT_NAME
    project_token = TEST_PROJECT_TOKEN

    # 1. Register Project
    reg_response = client.post(
        "/admin/project", json={"name": project_name, "token": project_token, "admin_key": MASTER_ADMIN_KEY}
    )
    assert reg_response.status_code == 200
    assert reg_response.json()["status"] == "registered"

    # 2. Send Log
    log_response = client.post(
        "/log",
        json={
            "project": project_name,
            "level": "info",
            "message": "Testing real integration message",
            "metadata": {"test": True},
        },
        headers={"Authorization": f"Bearer {project_token}"},
    )
    assert log_response.status_code == 200
    assert log_response.json()["ok"] is True

    # 3. Clean up (Delete Project)
    del_response = client.delete(f"/admin/project/{project_name}?admin_key={MASTER_ADMIN_KEY}")
    assert del_response.status_code == 200
    assert del_response.json()["status"] == "deleted"


def test_auth_failure_real(client):
    """Test kegagalan autentikasi pada endpoint log."""
    log_response = client.post(
        "/log",
        json={
            "project": "non-existent-project",
            "level": "info",
            "message": "Should fail",
        },
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert log_response.status_code in [401, 403]
