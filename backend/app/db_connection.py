"""PostgreSQL connection helper for URI credentials containing reserved characters."""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg


def database_connection_kwargs(database_url: str, **overrides):
    """Parse a PostgreSQL URI so reserved password characters cannot alter host parsing."""
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("Database URL must be a PostgreSQL URI with a hostname")
    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", [None])[0]
    params = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")) or "postgres",
    }
    if sslmode in {"require", "verify-ca", "verify-full"} or parsed.hostname.endswith("pooler.supabase.com"):
        params["ssl"] = "require"
    if "connect_timeout" in query:
        params["timeout"] = float(query["connect_timeout"][0])
    params.update(overrides)
    return params


async def connect_database(database_url: str, **overrides):
    return await asyncpg.connect(**database_connection_kwargs(database_url, **overrides))


async def create_database_pool(database_url: str, **overrides):
    return await asyncpg.create_pool(**database_connection_kwargs(database_url, **overrides))
