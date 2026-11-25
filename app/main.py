import hashlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.middleware.gzip import GZipMiddleware

from app import models
from app.config import settings
from app.database import Base, engine
from app.routers import (
    activity, aufgebot, auto_fines, chat, auth, branding, complaints, fine_types, duties, events, handballch, stats, documents, feedback, fines, games, media, notifications, polls, push, reports, users,
)

# Tabellen anlegen, falls noch nicht vorhanden (für den Start reicht das;
# später ggf. auf Alembic-Migrationen umstellen)
Base.metadata.create_all(bind=engine)
documents.seed_default_folders()
auth.ensure_admin_login()
fine_types.apply_seed()
from app.services.staff_seed import apply_staff_seed  # noqa: E402
apply_staff_seed()
from app.services.training_seed import apply_training_seed  # noqa: E402
apply_training_seed()
from app.services.guide_seed import apply_guide_seed  # noqa: E402
apply_guide_seed()

app = FastAPI(title="U19 Handball App")


# Komprimierung (gzip) fuer Seiten und Daten - nicht fuer Bilder/Dateien, die sind schon komprimiert
_NO_GZIP = ("/chat/img/", "/avatars/", "/logo", "/icon-", "/apple-touch-icon", "/badge", "/documents/", "/media/",
            "/guide-docx", "/reports/")


class _SelectiveGZip:
    def __init__(self, app):
        self.inner = app
        self.gz = GZipMiddleware(app, minimum_size=800, compresslevel=6)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope.get("path", "").startswith(_NO_GZIP):
            return await self.gz(scope, receive, send)
        return await self.inner(scope, receive, send)


app.add_middleware(_SelectiveGZip)

# Die App-Seite ist statisch: einmal laden, mit ETag ausliefern (Handy laedt sie nur neu, wenn sie sich geaendert hat)
_PAGE = (Path(__file__).parent / "templates" / "dashboard.html").read_bytes()
_PAGE_ETAG = '"' + hashlib.sha1(_PAGE).hexdigest()[:16] + '"'

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(games.router)
app.include_router(aufgebot.router)
app.include_router(polls.router)
app.include_router(fines.router)
app.include_router(notifications.router)
app.include_router(documents.router)
app.include_router(media.router)
app.include_router(reports.router)
app.include_router(complaints.router)
app.include_router(feedback.router)
app.include_router(branding.router)
app.include_router(events.router)
app.include_router(duties.router)
app.include_router(stats.router)
app.include_router(handballch.router)
app.include_router(activity.router)
app.include_router(fine_types.router)
app.include_router(push.router)
app.include_router(auto_fines.router)
app.include_router(chat.router)
app.include_router(chat.public)


@app.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    if request.headers.get("if-none-match") == _PAGE_ETAG:
        return Response(status_code=304, headers={"ETag": _PAGE_ETAG, "Cache-Control": "no-cache"})
    return Response(_PAGE, media_type="text/html; charset=utf-8", headers={"ETag": _PAGE_ETAG, "Cache-Control": "no-cache"})


@app.on_event("startup")
def _startup_sync():
    if settings.handball_auto_sync:
        handballch.background_sync()
    auto_fines.background()
    if settings.push_jobs:
        from app.services.push_jobs import background_jobs
        background_jobs()


@app.get("/health")
def health():
    return {"status": "ok"}
