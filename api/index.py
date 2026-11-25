# Einstiegspunkt fuer Vercel (Serverless). Vercel sucht in "api/" nach einer
# ASGI-App namens "app". Die eigentliche App liegt unveraendert in app/main.py,
# Render und der lokale Start (start.bat) funktionieren weiterhin wie bisher.
from app.main import app  # noqa: F401
