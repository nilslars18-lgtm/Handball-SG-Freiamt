"""
Legt einmalig den ersten Admin/Trainer-Account auf einer LIVE (gehosteten)
Instanz der App an - z.B. nach dem ersten Deploy auf Render.

Nutzung (im handball-app-Ordner, mit Python installiert):

    python scripts/bootstrap_admin_remote.py https://deine-app.onrender.com "Nils" nils@sgfreiamt.ch "SGF@2026"

Funktioniert nur EINMAL - danach existiert schon ein Admin und der
Endpunkt lehnt weitere Versuche ab (kein Sicherheitsproblem, einfach
nochmal normal einloggen).
"""
import json
import sys
import urllib.error
import urllib.request


def main() -> None:
    if len(sys.argv) != 5:
        print("Nutzung: python scripts/bootstrap_admin_remote.py <URL> <Name> <E-Mail> <Passwort>")
        print('Beispiel: python scripts/bootstrap_admin_remote.py https://handball-app.onrender.com "Nils" nils@sgfreiamt.ch "SGF@2026"')
        sys.exit(1)

    base_url, name, email, password = sys.argv[1:5]
    url = base_url.rstrip("/") + "/auth/bootstrap-admin"
    payload = {"name": name, "email": email, "password": password}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("Admin-Konto angelegt:", resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 400:
            print("Es existiert bereits ein Admin - einfach normal auf der Website einloggen.")
        else:
            print(f"Fehler ({e.code}):", body)
    except Exception as e:
        print("Konnte die Website nicht erreichen:", e)
        print("Ist die URL korrekt und die App bereits erfolgreich deployt?")


if __name__ == "__main__":
    main()
