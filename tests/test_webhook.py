"""Entegrasyon testleri — gerçek servisler gerektirir."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

import os
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("LLM_PROVIDER", "openrouter")

from src.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_webhook_invalid_secret():
    resp = client.post(
        "/webhook/jira",
        json={"issue": {"key": "TEST-1"}},
        headers={"X-Webhook-Secret": "yanlis-secret"},
    )
    assert resp.status_code == 401


def test_webhook_valid():
    with patch("src.main._process_ticket", new_callable=AsyncMock):
        resp = client.post(
            "/webhook/jira",
            json={"issue": {"key": "TEST-1"}},
            headers={"X-Webhook-Secret": "test-secret"},
        )
    assert resp.status_code == 202
    assert resp.json()["issue"] == "TEST-1"


def test_webhook_missing_issue_key():
    resp = client.post(
        "/webhook/jira",
        json={"issue": {}},
        headers={"X-Webhook-Secret": "test-secret"},
    )
    assert resp.status_code == 400
