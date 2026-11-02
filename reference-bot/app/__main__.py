import uvicorn

from app.config import get_settings
from app.main import create_app

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port)
