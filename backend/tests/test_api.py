import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
import sys
import os

# Add parent directory to path so we can import main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app, request_counts

client = TestClient(app)

def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["message"] == "AI Learning Path Generator API"

def test_health_check():
    """AP31 — /health reports the DATABASE, so it can say 'down'.

    This used to assert `{"status": "healthy"}`, a constant the route returned
    without touching anything. That is the shape of check that let a deleted
    database read as healthy for days on Poly_Tracker (PT22): it can only ever
    pass, so it tells you nothing.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "db": "up"}


def test_health_check_reports_a_dead_database():
    """The negative half — without it, the check above proves nothing.

    A health route is only worth having if it can FAIL, so make it fail: point
    the engine at a database that is not there and require a 503.
    """
    from sqlalchemy.exc import OperationalError

    with patch("main.engine.connect", side_effect=OperationalError("SELECT 1", {}, Exception("no such host"))):
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["db"] == "down"

@patch("routes.generate_learning_path")
def test_generate_learning_path(mock_generate):
    # Mock the AI response
    mock_generate.return_value = {
        "path_title": "Test Path",
        "path_description": "Test Description",
        "milestones": [
            {
                "title": "Test Milestone",
                "description": "Test Description",
                "estimated_hours": 5,
                "resources": ["Resource 1"]
            }
        ]
    }
    
    response = client.post("/api/generate", json={
        "goal": "Test Goal",
        "experience_level": "beginner",
        "time_commitment": "5-10 hours/week"
    })
    
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Path"
    assert len(data["milestones"]) == 1
    assert data["milestones"][0]["title"] == "Test Milestone"

def test_get_paths():
    response = client.get("/api/paths")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_rate_limiting():
    # Reset counts to ensure test isolation
    request_counts.clear()
    
    # Mock generation to avoid hitting API/Cache logic
    with patch("routes.generate_learning_path") as mock_generate:
        mock_generate.return_value = {
            "path_title": "Rate Limit Test",
            "path_description": "Desc",
            "milestones": []
        }
        
        # Make 6 requests (limit is 5)
        for i in range(6):
            response = client.post("/api/generate", json={
                "goal": f"Test {i}",
                "experience_level": "beginner",
                "time_commitment": "5-10 hours/week"
            })
            
            if i < 5:
                assert response.status_code == 200
            else:
                assert response.status_code == 429
                assert response.json()["detail"] == "Too many requests. Please try again later."
