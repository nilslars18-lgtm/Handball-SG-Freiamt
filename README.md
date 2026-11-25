# U19 Handball App — Alle 7 Features fertig

Login/Rollen, Kalender-Import, Aufgebot, Anfahrt, Essen-Umfrage,
Restaurantvorschläge, Bussenliste und Harz-Kasse sind gebaut und (bis auf
die beiden Google-API-Features, siehe unten) getestet.

## Neu: Dokumente, Fotos & Videos, Spielberichte

Auf der Startseite gibt es drei Kacheln:

- **📁 Dokumente** – Ordnerstruktur (Vorlagen, Verein, Team). Alle können
  ansehen/herunterladen, Trainer und Captain können Ordner anlegen, Dateien
  hochladen (max. 10 MB) und Info-Texte schreiben. Im Ordner „Vorlagen“
  liegt automatisch die Spielbericht-Vorlage (Word).
- **📸 Fotos & Videos** – Links (Google Drive, iCloud, YouTube …) einfügen
  und einem Spiel zuordnen. Es werden nur Links gespeichert.
- **📝 Spielberichte** – wer schreibt, wird automatisch reihum unter den
  aufgebotenen Spielern bestimmt (wer am wenigsten Berichte hat, ist dran).
  Neue/gelöschte Spieler werden automatisch berücksichtigt; der Trainer kann
  einen Schreiber auch fix setzen. Im Assistenten: Spiel wählen → Resultat →
  Teamfoto → Statistik → fertige Word-Datei, nur noch Text schreiben.

Dateien und Fotos liegen direkt in der Datenbank (Neon), weil Render Free
hochgeladene Dateien beim Einschlafen löscht. Fotos werden beim Hochladen
automatisch auf ca. 1600 px verkleinert.

## 24/7 online: Deployment auf Render + Neon (kostenlos)

Für den Dauerbetrieb (App über einen Link erreichbar, ohne dass dein PC
laufen muss) ist der Code bereits vorbereitet: `Procfile`, `render.yaml`
und Postgres-Unterstützung (`psycopg2-binary`, automatische Korrektur von
`postgres://`- zu `postgresql://`-URLs) sind fertig eingebaut und gegen
eine echte Postgres-Datenbank getestet.

**1. Neon-Datenbank erstellen (die Datenbank)**
1. Auf [neon.tech](https://neon.tech) mit GitHub einloggen, "New Project" klicken.
2. Nach dem Erstellen zeigt Neon eine **Connection String** an, z.B.
   `postgresql://user:pass@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require`
   — die brauchst du gleich für `DATABASE_URL`.

**2. Code zu GitHub hochladen**
1. Auf [github.com](https://github.com) ein neues, privates Repository erstellen (z.B. `handball-app`).
2. Diesen Ordner dort hochladen (z.B. per GitHub Desktop, oder `git init` / `git add .` / `git commit` / `git push` in diesem Ordner).

**3. Render-Web-Service erstellen (der Server)**
1. Auf [render.com](https://render.com) mit GitHub einloggen.
2. "New +" → "Blueprint" → dein `handball-app`-Repo auswählen. Render liest
   automatisch die `render.yaml` aus diesem Ordner und richtet Build-/Start-
   Befehl selbst ein.
   (Falls "Blueprint" nicht angezeigt wird: "New +" → "Web Service" →
   Repo wählen → Umgebung "Python" → Build Command `pip install -r
   requirements.txt` → Start Command `uvicorn app.main:app --host 0.0.0.0
   --port $PORT`.)
3. Unter "Environment" trägst du die Werte ein, die in `render.yaml` mit
   `sync: false` markiert sind:
   - `SECRET_KEY` — ein langer zufälliger Text (z.B. mit einem
     Passwort-Generator erzeugen)
   - `DATABASE_URL` — die Connection-String aus Schritt 1 von Neon
   - `GOOGLE_MAPS_API_KEY` — dein Google-Maps-Key (optional, kann später nachgetragen werden)
4. "Deploy" klicken. Nach ein paar Minuten zeigt Render den Live-Link an,
   z.B. `https://handball-app.onrender.com`.

**4. Ersten Admin-Account anlegen**
Einmalig lokal in diesem Ordner ausführen (Python muss installiert sein):
```
python scripts/bootstrap_admin_remote.py https://handball-app.onrender.com "Nils" nils@sgfreiamt.ch "SGF@2026"
```

**5. Fertig**
Link an dein Team schicken. Jede künftige Code-Änderung: neue Version ins
GitHub-Repo pushen → Render deployt automatisch neu, ohne dass du sonst
etwas tun musst.

⚠️ Hinweis: Der kostenlose Render-Plan "schläft" den Server nach 15 Min.
Inaktivität ein — der erste Aufruf danach dauert dann ca. 30–60 Sek. länger,
weil er erst wieder hochfahren muss. Live-Daten (Google Maps, Verfügbarkeits-
Klicks) funktionieren davon unabhängig immer normal, sobald die Seite geladen ist.

## Windows: Einfacher Start (empfohlen)

Einfach im entpackten Ordner auf **`start.bat`** doppelklicken.

Das Skript macht alles automatisch:
- prüft, ob Python installiert ist — falls nicht, lädt es Python 3.12
  herunter und installiert es (nur beim allerersten Mal, dauert 1-2 Min.)
- richtet eine eigene Python-Umgebung im Projektordner ein
- installiert alle benötigten Pakete
- startet den Server
- legt automatisch deinen Admin-Account an: `nils@sgfreiamt.ch` / `SGF@2026`
- öffnet die App im Browser unter http://127.0.0.1:8000

Beim ersten Start kann ein Windows-Sicherheitshinweis ("Windows hat den
Computer geschützt") erscheinen, weil das Skript nicht signiert ist — dort
auf "Weitere Informationen" → "Trotzdem ausführen" klicken.

Das kleine Fenster **"Handball App Server"**, das sich dabei öffnet, muss
offen bleiben, solange du die App benutzt. Zum Beenden dieses Fenster einfach
schliessen.

Falls beim ersten Durchlauf "Python wurde nicht gefunden" hängen bleibt:
einmal den PC neu starten und `start.bat` erneut ausführen (Windows braucht
nach der Python-Installation manchmal einen Neustart, um den neuen Pfad zu
kennen).

**Falls eine Fehlermeldung zu "pip" erscheint (z.B. "No module named pip"):**
Das passiert fast immer, wenn der Projektordner in OneDrive liegt (z.B. unter
`OneDrive - Firmenname\Desktop\...`) — OneDrive blockiert oder verzögert
manchmal Dateien, während die virtuelle Umgebung erstellt wird. `start.bat`
versucht das automatisch zu reparieren; falls das nicht klappt, den ganzen
Ordner `handball-app` an einen Ort **ausserhalb** von OneDrive kopieren
(z.B. nach `C:\HandballApp`) und von dort erneut starten.

## Manueller Start (Mac/Linux, oder falls start.bat nicht funktioniert)

```bash
cd handball-app
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # SECRET_KEY anpassen (irgendein langer Zufallsstring)

uvicorn app.main:app --reload
```

Die App läuft dann auf http://127.0.0.1:8000

- **http://127.0.0.1:8000/** → das Dashboard (Login + alle Features)
- **http://127.0.0.1:8000/docs** → interaktive API-Doku (Swagger), zum Ausprobieren
  aller Endpunkte ohne Frontend

## Ersten Admin/Trainer anlegen

Es gibt noch keinen User in der Datenbank. Lege dich einmalig per Swagger UI
(`/docs` → `POST /auth/bootstrap-admin`) oder per curl als Admin an:

```bash
curl -X POST http://127.0.0.1:8000/auth/bootstrap-admin \
  -H "Content-Type: application/json" \
  -d '{"name": "Dein Name", "email": "trainer@team.ch", "password": "geheim123"}'
```

Dieser Endpunkt funktioniert **nur einmal** — danach existiert bereits ein Admin
und du musst dich normal einloggen.

## Danach: Spieler anlegen (als Admin)

1. Login als Admin über `/docs` → `POST /auth/login` (username = E-Mail) → Token kopieren
2. Oben rechts in Swagger auf "Authorize" klicken, Token einfügen
3. `POST /auth/register` → legt einen neuen Spieler-Account an

## Datenbankschema

`app/models.py` enthält das vollständige Schema für alle 7 Features:

- `users` — Accounts, Rollen, Adresse (für Anfahrts-Berechnung)
- `games` — importierte Spiele (Feature 1)
- `aufgebote` — Aufgebotsstatus pro Spiel & Spieler (Feature 2)
- `essen_polls` / `poll_options` / `poll_votes` — Essens-Umfrage (Feature 4/5)
- `fines` — Bussenliste (Feature 6)
- `harz_expenses` — Harz-Ausgaben, gemeinsamer Topf mit Bussenkasse (Feature 7)

Für den Start ist `DATABASE_URL=sqlite:///./handball.db` gesetzt. Für den
späteren Umzug auf Railway/Render reicht es, `DATABASE_URL` auf eine
Postgres-Verbindung zu ändern — der Code selbst muss dafür nicht angepasst
werden (SQLAlchemy übernimmt das).

## Alle 7 Features (Stand: fertig)

Login/Rollen, ICS-Import (Feature 1) und alle weiteren Features sind jetzt
gebaut. Bedienung über das **Dashboard unter `/`** (nicht mehr nur `/docs`).

### Feature 1: ICS-Kalender-Import

Getestet mit einem echten handball.ch-Export der SG Freiamt PLUS.

- `UID` (z.B. `512042@www.handball.ch`) → zuverlässige Dedup-ID
- `DTSTART`/`DTEND` → naive lokale Zeit (Europe/Zurich)
- `SUMMARY`: `"<Liga-Kürzel> - <Team A> - <Team B>"` → unser Team (Standard:
  `"SG Freiamt PLUS"`, änderbar über `TEAM_NAME` in `.env`) wird erkannt,
  Reihenfolge zeigt Heim/Auswärts
- `LOCATION` → nur Hallenname → wird bei gesetztem `GOOGLE_MAPS_API_KEY`
  über Places Text-Search zu einer echten Adresse aufgelöst

Admin: Tab "Verwaltung" → `.ics`-Datei hochladen. Erneuter Import legt
nichts doppelt an (getestet).

### Feature 2: Aufgebot

Trainer setzt ab `AUFGEBOT_DAYS_BEFORE` Tagen (Standard 7) vor dem Spiel
pro Spieler den Status (Tab "Verwaltung" → Spiel wählen). Vorher/für alle
anderen: neutraler Status. Farbcodierung im Spielplan:
🟢 aufgeboten · 🟠 verletzt/Bank · 🔴 nicht aufgeboten · ⚪ Aufgebot folgt.
**Getestet.**

### Feature 3: Anfahrt mit Live-Verkehr

Im Spieldetail → "Abfahrtszeit berechnen". Nutzt die Google Directions API
zweistufig (grobe Fahrzeit → damit Abfahrtszeitpunkt schätzen → mit diesem
Zeitpunkt nochmal abfragen für die vorhergesagte Verkehrslage). Braucht
`GOOGLE_MAPS_API_KEY` in `.env` sowie eine hinterlegte eigene Adresse
(Profil); ohne Key erscheint ein klarer Hinweis statt eines Fehlers.
**Logik fertig, Live-Abfrage nicht in meiner Umgebung testbar — bitte bei
dir mit echtem Key ausprobieren.**

### Feature 4: Essen-Umfrage

Öffnet automatisch Donnerstag 14:00 (konfigurierbar über `POLL_OPEN_*` in
`.env`) vor dem Spiel, schliesst 30 Min vor Spielbeginn. Während offen:
komplett anonym, jeder sieht nur seine eigene Stimme. Auswertung (inkl. wer
mitkommt) wird erst **nach Spielende** sichtbar — dafür wird beim
ICS-Import automatisch `DTEND` gespeichert (falls in der Datei vorhanden,
sonst Schätzung über `GAME_DEFAULT_DURATION_MINUTES`). **Vollständig
getestet:** Abstimmen während offen, 403 vor Spielende, korrekte
Prozentzahlen und Mitkommende-Liste danach.

### Feature 5: Restaurantvorschläge

Basierend auf der/den Gewinner-Option(en) aus Feature 4 (bei echtem
Gleichstand: Vorschläge für beide). Sucht offene Restaurants in der Nähe
der Halle über Google Places. Nur sichtbar, sobald auch die Umfrage-
Ergebnisse sichtbar sind (also nach Spielende). Braucht ebenfalls
`GOOGLE_MAPS_API_KEY` — **Logik fertig, Live-Test braucht echten Key.**
Vereinfachung: die Suche geht von der Halle aus, nicht von einer exakten
Berechnung "Richtung Heimweg der Mehrheit" (das wäre ohne echte
Adressdaten aller Mitfahrenden kaum sinnvoll umsetzbar).

### Feature 6+7: Bussenliste & Harz-Kasse

Ein gemeinsamer Topf: Harz-Ausgaben werden direkt vom Kassenstand der
Bussenkasse abgezogen, keine separate Kasse. Bussenchef trägt Bussen ein
und markiert sie als bezahlt; Harzchef trägt Ausgaben ein; beide sehen den
Kassenstand (auch wer nur eine der beiden Rollen hat). **Vollständig
getestet**, inkl. korrekter Kassenstand-Berechnung (bezahlte Bussen minus
Harz-Ausgaben).

## Einstellungen (Tab "Mehr" / Feature: Einstellungen)

Für alle User:
- **Dark/Light Mode** – Umschalter, wird pro Gerät in `localStorage` gemerkt.
- **Passwort ändern** – jeder User kann sein eigenes Passwort ändern
  (`PUT /users/me/password`, verlangt das aktuelle Passwort).
- **Benachrichtigungs-Badge** oben links (blinkende Zahl) – zählt offene
  Essen-Umfragen, an denen man noch nicht teilgenommen hat, plus eigene
  offene Bussen. Verschwindet automatisch, sobald abgestimmt bzw. die Busse
  vom Bussenchef als bezahlt markiert wurde (`GET /notifications/count`,
  wird beim Öffnen und alle 45s automatisch neu geladen).

Nur für Admin:
- **Spieler löschen** – deaktiviert den Account (Historie wie Bussen/Aufgebote
  bleibt erhalten, der Spieler kann sich aber nicht mehr einloggen und taucht
  nirgends mehr auf). Admin-Accounts können nicht gelöscht werden.
  Spieler *anlegen* geht weiterhin über den Tab "Verwaltung".
- **Passwort eines Spielers einsehen** – zeigt das zuletzt gesetzte Passwort
  im Klartext (`GET /users/{id}/password`).

⚠️ **Sicherheitshinweis zur Passwort-Einsicht:** Normalerweise werden
Passwörter nur als unumkehrbarer Hash (bcrypt) gespeichert – so ist es
technisch unmöglich, sie im Klartext wieder anzuzeigen, auch für den Admin,
selbst wenn die Datenbank gestohlen würde. Weil du das aber ausdrücklich so
wolltest, speichert die App zusätzlich eine Klartext-Kopie des zuletzt
gesetzten Passworts pro User (`last_plaintext_password` in der Datenbank),
nur um sie dem Admin anzeigen zu können. Für den Login selbst wird weiterhin
ausschliesslich der Hash verwendet. Das ist ein bewusster Kompromiss bei der
Sicherheit: Wer Zugriff auf die Datenbank-Datei (`handball.db`) bekommt,
kann damit alle aktuellen Passwörter im Klartext auslesen. Falls dir das zu
riskant ist, kann ich das jederzeit auf eine sicherere Variante umstellen,
z. B. "Admin setzt ein neues, zufälliges Passwort und es wird dem Spieler
einmalig angezeigt" statt dauerhafter Klartext-Speicherung.

- **Bussen anpassen** – Bussenchef und Admin können bei einer bestehenden
  Busse (in der Spieler-Detailansicht bei "Bussen") Grund und Betrag über
  den ✏️-Button nachträglich ändern (`PATCH /fines/{id}`).

## Rollen

- **base_role**: `admin` (Trainer/Admin) oder `player`
- Zusatz-Flags: `is_captain`, `is_bussenchef`, `is_harzchef` (kombinierbar)
- Admin verwaltet Rollen im Tab "Verwaltung" (oder `PATCH /users/{id}/roles`)
- Admin darf überall auch als Bussenchef/Harzchef handeln (Fallback)

## Konfiguration (`.env`)

Alle Zeitfenster, der Teamname und die Umfrage-Optionen sind über `.env`
einstellbar (siehe `.env.example` für alle Werte und Erklärungen) — nichts
davon ist im Code hart verdrahtet.

## Bekannte Einschränkungen / offene Punkte

- Feature 3 & 5 brauchen einen echten `GOOGLE_MAPS_API_KEY` zum Live-Testen
- Aktuell nur SQLite; für den Railway/Render-Umzug reicht das Ändern von
  `DATABASE_URL` auf eine Postgres-Verbindung
- Kein automatisches "Umfrage-Reminder" (Push/E-Mail) — die App zeigt den
  Status nur an, wenn man sie öffnet
- Noch kein automatisiertes Backup der SQLite-Datei

## Nächste Schritte

Am besten einmal `start.bat` ausführen (holt sich automatisch die neuen
Dateien/Abhängigkeiten) und durchklicken: Spielplan importieren, ein Spiel
aufbieten, bei der Essen-Umfrage abstimmen, eine Busse eintragen. Sag mir,
was gut funktioniert und wo etwas hakt oder anders sein soll — dann
schleife ich das direkt ein.

## App-Installation, Push-Benachrichtigungen, Kalender-Abo

- **Als App installieren (PWA):** `/manifest.webmanifest`, Service Worker `/sw.js`, Icons in `app/static/`.
  iPhone: Safari → Teilen → Zum Home-Bildschirm. Android: Chrome → ⋮ → App installieren.
- **Push:** eigene Web-Push-Umsetzung (`app/services/webpush.py`, RFC 8291/8292, nur `cryptography`).
  Die VAPID-Schlüssel werden beim ersten Start erzeugt und in `app_assets` (Key `vapid_private`) gespeichert.
  Optional per Umgebungsvariable `VAPID_PRIVATE_PEM` vorgeben. Auf dem iPhone geht Push nur aus der installierten App (iOS 16.4+).
  - Sofort: Aufgebot, neuer/verschobener/abgesagter Termin, neue Trainingszeiten, Erinnerung vom Trainer,
    neue Busse, Beschwerde entschieden; Admin: Ab-/Verletztmeldungen.
  - Automatisch alle 10 Min. (`app/services/push_jobs.py`, nicht zwischen 21:30 und 07:00):
    Ämtli am Vortag, „Bitte eintragen“ ca. 30 Std. vor dem Termin, Spielbericht vor/nach dem Spiel. Jede Meldung nur einmal.
  - Jeder Spieler kann unter Mehr → Benachrichtigungen einzelne Arten ausschalten. Abschalten der Jobs: `PUSH_JOBS=false`.
- **Kalender-Abo:** persönlicher Link `/cal/<token>.ics` (Mehr → Termine im Handy-Kalender), mit Aufgebot, Ämtli und Antwort.
