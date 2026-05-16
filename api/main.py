from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from api.routes.cn_analysis import router as cn_analysis_router


app = FastAPI(title="TradingAgents China Market API")
app.include_router(cn_analysis_router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/visualization", include_in_schema=False)
def visualization() -> FileResponse:
    return FileResponse(STATIC_DIR / "visualization.html")
