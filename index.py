# Einstiegspunkt fuer Vercel: Vercel sucht im Hauptordner nach "index.py" und
# erwartet dort eine ASGI-App namens "app". Die eigentliche App liegt unveraendert
# in app/main.py (Render und start.bat funktionieren weiterhin wie bisher).
from app.main import app  # noqa: F401
