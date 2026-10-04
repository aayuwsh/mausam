import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import security
from backend.app.main import app, ready


class SecurityAndReadinessTests(unittest.TestCase):
    def test_missing_token_is_unauthorized(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(security.supabase_user(None))
        self.assertEqual(caught.exception.status_code, 401)

    def test_supabase_timeout_is_service_unavailable(self):
        class TimeoutClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, *args, **kwargs): raise httpx.ReadTimeout("private transport detail")

        with patch.object(security.settings, "supabase_url", "https://auth.example.invalid"), \
             patch.object(security.settings, "supabase_anon_key", "test-key"), \
             patch.object(security.httpx, "AsyncClient", TimeoutClient):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(security.supabase_user("Bearer sample-token"))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private transport detail", caught.exception.detail)

    def test_database_unavailable_is_not_ready(self):
        app.state.db = None
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(ready())
        self.assertEqual(caught.exception.status_code, 503)

    def test_application_starts_degraded_and_readiness_reports_database_failure(self):
        async def fail_connect(*args, **kwargs):
            raise OSError("hidden connection detail")

        with patch("backend.app.main.settings.database_url", "postgresql://unit-test"), \
             patch("backend.app.main.create_database_pool", fail_connect), \
             TestClient(app) as client:
            health = client.get("/api/v1/health")
            ready_response = client.get("/api/v1/ready")
            district_response = client.get("/api/v1/locations?level=district")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["database"], "unreachable")
        self.assertEqual(ready_response.status_code, 503)
        self.assertNotIn("hidden connection detail", ready_response.text)
        self.assertEqual(len(district_response.json()["locations"]), 3)


if __name__ == "__main__":
    unittest.main()
