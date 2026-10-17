from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from preflight.server.api.v1 import router as api_v1

UI_DIR = Path(__file__).parent / "ui"
templates = Jinja2Templates(directory=UI_DIR / "templates")

app = FastAPI(title="Preflight")
app.include_router(api_v1)
app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")
