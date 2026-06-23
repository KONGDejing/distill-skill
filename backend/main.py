from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database import init_db
from routers import bloggers, videos, scripts, generated_videos, settings as settings_router

app = FastAPI(title="短视频AI内容工厂", version="0.1.0")

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

app.mount("/storage", StaticFiles(directory="storage"), name="storage")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}
