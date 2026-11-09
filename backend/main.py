from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database import init_db
from routers import bloggers, videos, scripts, generated_videos, settings as settings_router, factory
from config import settings
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import HTTPException

app = FastAPI(title="短视频 AI 内容工厂", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bloggers.router, prefix="/api/bloggers", tags=["bloggers"])
app.include_router(videos.router, prefix="/api/videos", tags=["videos"])
app.include_router(scripts.router, prefix="/api/scripts", tags=["scripts"])
app.include_router(generated_videos.router, prefix="/api/videos-generated", tags=["generated_videos"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(factory.router, prefix="/api/factory", tags=["factory"])

app.mount("/storage", StaticFiles(directory=str(settings.STORAGE_DIR)), name="storage")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Production serves the built UI and API on one port. Vite remains available for development.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend_assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend_page(path: str):
    if path.startswith(("api/", "storage/", "assets/")) or not (FRONTEND_DIST / "index.html").is_file():
        raise HTTPException(404, "页面或接口不存在")
    return FileResponse(FRONTEND_DIST / "index.html")
