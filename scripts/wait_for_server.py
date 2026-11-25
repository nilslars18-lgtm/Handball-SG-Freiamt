"""Wartet bis der lokale Server unter /health antwortet (max. 30 Sekunden)."""
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/health"

for _ in range(30):
    try:
        urllib.request.urlopen(URL, timeout=2)
        print("Server ist bereit.")
        break
    except Exception:
        time.sleep(1)
else:
    print("Server antwortet nicht - evtl. Fehler beim Start (siehe Serverfenster).")
