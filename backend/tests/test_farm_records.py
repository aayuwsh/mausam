import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.security import supabase_user


USER_ID = "11111111-1111-4111-8111-111111111111"
PROFILE_ID = "22222222-2222-4222-8222-222222222222"
RECORD_ID = "33333333-3333-4333-8333-333333333333"
CLIENT_ID = "44444444-4444-4444-8444-444444444444"


class FakeDatabase:
    def __init__(self):
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "SELECT id FROM farmer_profiles" in query:
            return {"id": UUID(PROFILE_ID)}
        if "INSERT INTO farm_records" in query:
            return {
                "id": UUID(RECORD_ID),
                "client_id": args[1],
                "record_type": args[2],
                "recorded_on": args[3],
                "title": args[4],
                "details": args[5],
                "quantity": args[6],
                "unit": args[7],
                "amount": args[8],
                "metadata": {"crop": "wheat"},
                "created_at": "2026-10-04T00:00:00+00:00",
            }
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return []

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return "DELETE 1"

    async def close(self):
        return None


class FarmRecordEndpointTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        app.dependency_overrides[supabase_user] = lambda: {"id": USER_ID}

        async def fake_pool(*args, **kwargs):
            return self.db

        self.pool_patch = patch("backend.app.main.create_database_pool", fake_pool)
        self.url_patch = patch("backend.app.main.settings.database_url", "postgresql://test")
        self.pool_patch.start()
        self.url_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.pop(supabase_user, None)
        self.url_patch.stop()
        self.pool_patch.stop()

    def test_list_records_is_scoped_to_the_authenticated_farmer(self):
        response = self.client.get("/api/v1/farm-records/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"records": [], "storage": "database"})
        self.assertEqual(self.db.queries[0][1], (UUID(USER_ID),))
        self.assertIn("WHERE farmer_id=$1", self.db.queries[1][0])
        self.assertEqual(self.db.queries[1][1], (UUID(PROFILE_ID),))

    def test_create_record_returns_database_record_and_uses_idempotency_key(self):
        response = self.client.post(
            "/api/v1/farm-records/me",
            json={
                "client_id": CLIENT_ID,
                "record_type": "activity",
                "recorded_on": "2026-10-04",
                "title": "  Irrigated field  ",
                "details": "  First watering  ",
                "metadata": {"crop": "wheat"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["storage"], "database")
        self.assertEqual(payload["record"]["title"], "Irrigated field")
        self.assertEqual(payload["record"]["id"], RECORD_ID)
        insert_query, insert_args = self.db.queries[1]
        self.assertIn("ON CONFLICT (farmer_id,client_id)", insert_query)
        self.assertEqual(insert_args[0], UUID(PROFILE_ID))
        self.assertEqual(insert_args[1], UUID(CLIENT_ID))

    def test_create_rejects_invalid_record_type(self):
        response = self.client.post(
            "/api/v1/farm-records/me",
            json={"record_type": "unknown", "recorded_on": "2026-10-04", "title": "Test"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.queries, [])

    def test_delete_is_scoped_by_user_profile_and_record_id(self):
        response = self.client.delete(f"/api/v1/farm-records/me/{RECORD_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "deleted"})
        query, args = self.db.queries[0]
        self.assertIn("DELETE FROM farm_records fr USING farmer_profiles fp", query)
        self.assertIn("fp.auth_user_id=$1 AND fr.id=$2", query)
        self.assertEqual(args, (UUID(USER_ID), UUID(RECORD_ID)))

    def test_delete_reports_missing_record_without_affecting_another_user(self):
        async def not_found(query, *args):
            self.db.queries.append((query, args))
            return "DELETE 0"

        self.db.execute = not_found
        response = self.client.delete(f"/api/v1/farm-records/me/{RECORD_ID}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Farm record not found.")


if __name__ == "__main__":
    unittest.main()
