"""
Legt beim allerersten Start automatisch den Admin-Account an, damit Nils
sich sofort einloggen kann. Wird von start.bat nach dem Serverstart
aufgerufen. Falls schon ein Admin existiert (z.B. bei jedem weiteren Start),
passiert einfach nichts - kein Fehler.
"""
import json
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/auth/bootstrap-admin"
PAYLOAD = {"name": "Nils", "email": "nils@sgfreiamt.ch", "password": "SGF@2026"}


def main() -> None:
    data = json.dumps(PAYLOAD).encode("utf-8")
    req = urllib.request.Request(
        URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    for _ in range(10):
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                print("Admin-Konto angelegt:", resp.read().decode())
                return
        except urllib.error.HTTPError as e:
            if e.code == 400:
                print("Admin-Konto existiert bereits - alles gut, einfach einloggen.")
                return
            time.sleep(1)
        except Exception:
            time.sleep(1)
    print("Konnte Admin-Konto nicht automatisch anlegen (Server evtl. noch nicht bereit).")


if __name__ == "__main__":
    main()
