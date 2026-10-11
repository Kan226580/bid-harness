from fastapi import FastAPI

from backend.app.api.errors import install_error_handlers
from backend.app.api.middleware import OriginCheckMiddleware, RequestIdMiddleware
from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.members import router as members_router
from backend.app.api.v1.projects import router as projects_router

app = FastAPI(
    title="工程标书 AI Harness Agent", swagger_ui_parameters={"persistAuthorization": True}
)

app.add_middleware(OriginCheckMiddleware)
app.add_middleware(RequestIdMiddleware)

install_error_handlers(app)
app.include_router(auth_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(members_router, prefix="/api/v1")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}
