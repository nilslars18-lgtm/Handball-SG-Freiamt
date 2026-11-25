@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   U19 Handball App wird gestartet
echo ============================================
echo.

set "PYEXE=python"

where python >nul 2>nul
if %errorlevel% neq 0 goto :installpython
python --version >nul 2>nul
if %errorlevel% neq 0 goto :installpython
goto :havepython

:installpython
echo Python wurde nicht gefunden - lade Python 3.12 herunter...
echo (Das passiert nur beim allerersten Start, dauert ein bis zwei Minuten)
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\python-installer.exe'"

if not exist "%TEMP%\python-installer.exe" (
    echo.
    echo Download ist fehlgeschlagen. Bitte pruefe deine Internetverbindung
    echo oder installiere Python manuell von https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Installiere Python still im Hintergrund...
start /wait "" "%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\python-installer.exe"

set "PY_DIR=%LocalAppData%\Programs\Python\Python312"
set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
set "PYEXE=%PY_DIR%\python.exe"

if not exist "%PYEXE%" (
    echo.
    echo Python-Installation wurde nicht am erwarteten Ort gefunden.
    echo Bitte dieses Fenster schliessen, deinen PC einmal neu starten
    echo und start.bat danach erneut ausfuehren.
    pause
    exit /b 1
)

:havepython
echo Python gefunden:
"%PYEXE%" --version
echo.

if not exist venv (
    echo Erstelle virtuelle Umgebung, einmalig beim ersten Start...
    "%PYEXE%" -m venv venv
)

rem Pruefen ob die venv ein funktionierendes pip hat - falls nicht (kommt vor
rem allem vor, wenn der Ordner in OneDrive liegt und die venv unvollstaendig
rem synchronisiert wurde), venv neu erstellen und pip explizit nachruesten
venv\Scripts\python.exe -m pip --version >nul 2>nul
if errorlevel 1 (
    echo Virtuelle Umgebung ist unvollstaendig - wird neu erstellt...
    rmdir /s /q venv >nul 2>nul
    "%PYEXE%" -m venv venv
    venv\Scripts\python.exe -m ensurepip --upgrade >nul 2>nul
)

venv\Scripts\python.exe -m pip --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo Die virtuelle Umgebung bekommt kein funktionierendes pip. Das passiert
    echo fast immer, wenn der Projektordner in OneDrive liegt ^(bei dir z.B.
    echo unter "OneDrive - ..."^) und OneDrive Dateien waehrend der Erstellung
    echo blockiert oder verzoegert synchronisiert.
    echo.
    echo Loesung: Kopiere den ganzen Ordner "handball-app" an einen Ort
    echo AUSSERHALB von OneDrive, z.B. nach C:\HandballApp, und fuehre
    echo start.bat von dort aus erneut aus.
    pause
    exit /b 1
)

echo Installiere/aktualisiere Abhaengigkeiten...
venv\Scripts\python.exe -m pip install -q --upgrade pip
venv\Scripts\python.exe -m pip install -q -r requirements.txt

if not exist .env (
    copy .env.example .env >nul
)

echo.
echo Starte Server...
start "Handball App Server - bitte offen lassen" /min cmd /c "venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"

echo Warte bis der Server bereit ist...
venv\Scripts\python.exe scripts\wait_for_server.py

echo Lege Admin-Konto an (nur beim ersten Mal noetig)...
venv\Scripts\python.exe scripts\bootstrap_admin.py

echo Oeffne die App im Browser...
start "" http://127.0.0.1:8000

echo.
echo ============================================
echo   Fertig! Die App laeuft unter:
echo   http://127.0.0.1:8000
echo.
echo   Login: nils@sgfreiamt.ch / SGF@2026
echo.
echo   Wichtig: Das Fenster "Handball App Server"
echo   muss offen bleiben, solange du die App
echo   benutzen willst. Dieses Fenster hier kannst
echo   du jetzt schliessen.
echo ============================================
pause
