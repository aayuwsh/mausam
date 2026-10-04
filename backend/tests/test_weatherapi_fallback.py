import asyncio
import unittest
from unittest.mock import patch

import httpx

from backend.app import main


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "location": {"tz_id": "Asia/Kolkata"},
            "current": {
                "last_updated_epoch": 1791133200, "temp_c": 30.5, "feelslike_c": 34.0,
                "humidity": 70, "precip_mm": 0.2, "wind_kph": 8.0, "wind_degree": 120,
                "cloud": 45, "pressure_mb": 1008.0,
                "condition": {"code": 1003, "text": "Partly cloudy"},
            },
            "forecast": {"forecastday": [{
                "date": "2026-10-04",
                "day": {"condition": {"code": 1183}, "maxtemp_c": 32, "mintemp_c": 24,
                        "totalprecip_mm": 1.2, "daily_chance_of_rain": 60, "maxwind_kph": 12},
            }]},
        }


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse()


class WeatherApiFallbackTests(unittest.TestCase):
    def test_weatherapi_codes_map_to_wmo_codes(self):
        self.assertEqual(main._weatherapi_weather_code(1000), 0)
        self.assertEqual(main._weatherapi_weather_code(1003), 2)
        self.assertEqual(main._weatherapi_weather_code(1183), 61)
        self.assertEqual(main._weatherapi_weather_code(1246), 82)
        self.assertIsNone(main._weatherapi_weather_code(9999))

    def test_weatherapi_response_normalizes_only_supplied_fields(self):
        with patch.object(main.settings, "weatherapi_api_key", "test-key"), \
             patch.object(httpx, "AsyncClient", _FakeAsyncClient):
            forecast = asyncio.run(main._weatherapi_live_forecast(httpx, 26.6, 84.9))

        self.assertEqual(forecast["current"]["temperature_2m"], 30.5)
        self.assertEqual(forecast["current"]["weather_code"], 2)
        self.assertEqual(forecast["daily"]["weather_code"], [61])
        self.assertEqual(forecast["daily"]["precipitation_sum"], [1.2])
        self.assertEqual(forecast["current"]["soil_moisture_0_to_1cm"], None)
        self.assertEqual(forecast["daily"]["shortwave_radiation_sum"], [None])

    def test_fallback_is_disabled_without_server_key(self):
        with patch.object(main.settings, "weatherapi_api_key", None):
            result = asyncio.run(main._weatherapi_live_forecast(httpx, 26.6, 84.9))
        self.assertIsNone(result)

    def test_open_meteo_cooldown_uses_weatherapi_fallback(self):
        location_id = "IN-LGD-D-10-213"
        cache = getattr(main.app.state, "live_weather_cache", {})
        cache.pop((location_id, False), None)
        main.app.state.live_weather_cache = cache
        prior_retry_at = getattr(main.app.state, "open_meteo_forecast_retry_at", 0)
        main.app.state.open_meteo_forecast_retry_at = main.time.monotonic() + 60
        normalized = {"current": {"temperature_2m": 30.5}, "daily": {"time": ["2026-10-04"]}}
        try:
            with patch.object(main.settings, "weatherapi_api_key", "test-key"), \
                 patch.object(main, "_weatherapi_live_forecast", return_value=normalized):
                result = asyncio.run(main.live_location_weather(location_id, include_seasonal=False))
        finally:
            main.app.state.open_meteo_forecast_retry_at = prior_retry_at
            getattr(main.app.state, "live_weather_cache", {}).pop((location_id, False), None)

        self.assertEqual(result["provider"], "WeatherAPI.com")
        self.assertEqual(result["forecast"]["current"]["temperature_2m"], 30.5)
        self.assertEqual(result["horizons"], [])


if __name__ == "__main__":
    unittest.main()
