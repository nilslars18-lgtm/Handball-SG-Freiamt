from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import models
from app.database import Base, engine
from app.routers import aufgebot, auth, complaints, feedback, fines, games, notifications, polls, users

# Tabellen anlegen, falls noch nicht vorhanden (für den Start reicht das;
# später ggf. auf Alembic-Migrationen umstellen)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="U19 Handball App")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(games.router)
app.include_router(aufgebot.router)
app.include_router(polls.router)
app.include_router(fines.router)
app.include_router(notifications.router)
app.include_router(complaints.router)
app.include_router(feedback.router)


@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok"}
