import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.app import main


class _FakeAsyncClient:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.requests.append(url)
        return self.responses.pop(0)


class GeminiFallbackTests(unittest.TestCase):
    def test_retries_primary_then_uses_fallback_on_provider_outage(self):
        _FakeAsyncClient.requests = []
        _FakeAsyncClient.responses = [
            httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}, request=httpx.Request("POST", "https://test.invalid")),
            httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}, request=httpx.Request("POST", "https://test.invalid")),
            httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Rice needs a well-prepared field."}]}}]}, request=httpx.Request("POST", "https://test.invalid")),
        ]
        with patch.object(main.settings, "gemini_api_key", "test-key"), \
             patch.object(main.settings, "gemini_model", "gemini-primary"), \
             patch.object(main.settings, "gemini_fallback_model", "gemini-fallback"), \
             patch.object(httpx, "AsyncClient", _FakeAsyncClient), \
             patch.object(main, "random") as random_module, \
             patch.object(main.asyncio, "sleep", new=AsyncMock()), \
             patch.object(main, "logger"):
            random_module.uniform.return_value = 0
            payload, model = asyncio.run(main._gemini_generate_content(
                httpx,
                system_instruction="Answer simply.",
                contents=[{"role": "user", "parts": [{"text": "Rice advice"}]}],
                generation_config={"maxOutputTokens": 50},
            ))

        self.assertEqual(model, "gemini-fallback")
        self.assertIn("Rice needs", payload["candidates"][0]["content"]["parts"][0]["text"])
        self.assertEqual(len(_FakeAsyncClient.requests), 3)
        self.assertTrue(_FakeAsyncClient.requests[0].endswith("/gemini-primary:generateContent"))
        self.assertTrue(_FakeAsyncClient.requests[2].endswith("/gemini-fallback:generateContent"))

    def test_does_not_retry_quota_or_credential_errors_on_fallback(self):
        _FakeAsyncClient.requests = []
        _FakeAsyncClient.responses = [httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}, request=httpx.Request("POST", "https://test.invalid"))]
        with patch.object(main.settings, "gemini_api_key", "test-key"), \
             patch.object(main.settings, "gemini_model", "gemini-primary"), \
             patch.object(main.settings, "gemini_fallback_model", "gemini-fallback"), \
             patch.object(httpx, "AsyncClient", _FakeAsyncClient), \
             patch.object(main, "logger"):
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(main._gemini_generate_content(
                    httpx,
                    system_instruction="Answer simply.",
                    contents=[{"role": "user", "parts": [{"text": "Rice advice"}]}],
                    generation_config={"maxOutputTokens": 50},
                ))

        self.assertEqual(len(_FakeAsyncClient.requests), 1)

    def tearDown(self):
        _FakeAsyncClient.responses = []


if __name__ == "__main__":
    unittest.main()
