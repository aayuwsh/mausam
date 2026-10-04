from typing import Any, Protocol


class WeatherProvider(Protocol):
    async def forecast(self, latitude: float, longitude: float) -> dict[str, Any]: ...


class ClimateIndexProvider(Protocol):
    async def latest(self) -> list[dict[str, Any]]: ...


class GeographicDataProvider(Protocol):
    async def children(self, parent_id: str | None, level: str) -> list[dict[str, Any]]: ...


class AgriculturalDataProvider(Protocol):
    async def active_calendar(self, location_id: str, season: str) -> list[dict[str, Any]]: ...


class TranslationProvider(Protocol):
    async def translate(self, text: str, target_language: str) -> str: ...


class NotificationProvider(Protocol):
    async def send(self, phone: str, message: str, language: str) -> dict[str, Any]: ...


class OpenMeteoSeasonalProvider:
    """Raw ECMWF EC46 data only. This does not produce Mausam probabilities/advice."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    async def forecast(self, latitude: float, longitude: float) -> dict[str, Any]:
        import httpx

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "models": "ecmwf_ec46",
            "forecast_days": 46,
            "daily": "precipitation_sum,temperature_2m_mean,relative_humidity_2m_mean,wind_speed_10m_mean",
            "timezone": "Asia/Kolkata",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        return {
            "provider": "Open-Meteo / ECMWF EC46",
            "source_url": str(response.url),
            "retrieved_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "model_run_time": payload.get("generationtime_ms"),
            "resolution_km": 36,
            "bias_corrected": False,
            "data": payload,
        }
