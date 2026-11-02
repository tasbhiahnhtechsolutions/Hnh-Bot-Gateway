import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import AppEnv, Settings
from app.main import create_app


async def test_ready_with_real_database_and_redis(
    migrated_url: str, redis_url: str, engine: AsyncEngine
) -> None:
    settings = Settings(app_env=AppEnv.TEST, database_url=migrated_url, redis_url=redis_url)
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}
    await app.state.redis.aclose()
    await app.state.engine.dispose()
