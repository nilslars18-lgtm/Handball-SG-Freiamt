from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import require_admin
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserOut)
def register_user(
    user_in: schemas.UserCreate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Nur der Admin/Trainer legt neue Spieler-Accounts an."""
    existing = db.query(models.User).filter(models.User.email == user_in.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="E-Mail bereits registriert")

    user = models.User(
        name=user_in.name,
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        last_plaintext_password=user_in.password,
        base_role=models.BaseRole.PLAYER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/bootstrap-admin", response_model=schemas.UserOut)
def bootstrap_admin(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Einmalige Einrichtung: legt den ersten Admin/Trainer an.
    Funktioniert nur, solange noch KEIN Admin existiert - danach 400.
    """
    existing_admin = (
        db.query(models.User).filter(models.User.base_role == models.BaseRole.ADMIN).first()
    )
    if existing_admin:
        raise HTTPException(
            status_code=400, detail="Es existiert bereits ein Admin. Bitte normal einloggen."
        )

    user = models.User(
        name=user_in.name,
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        last_plaintext_password=user_in.password,
        base_role=models.BaseRole.ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _find_login_user(db: Session, ident: str):
    """Benutzer finden ueber Login (E-Mail/Vorname), vollen Namen oder eindeutigen Vornamen."""
    ident = " ".join(ident.split()).lower()  # Tabs/Leerzeichen vom Kopieren entfernen
    if not ident:
        return None
    active = db.query(models.User).filter(models.User.is_active == True)  # noqa: E712
    user = active.filter(func.lower(models.User.email) == ident).first()
    if user:
        return user
    user = active.filter(func.lower(models.User.name) == ident).first()
    if user:
        return user
    same_first = [u for u in active.all() if u.name.strip().lower().split(" ")[0] == ident]
    return same_first[0] if len(same_first) == 1 else None


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = _find_login_user(db, form_data.username)
    password = form_data.password.strip()  # versehentliche Leerzeichen/Tabs beim Kopieren
    if not user or not (verify_password(password, user.password_hash)
                        or verify_password(form_data.password, user.password_hash)):
        raise HTTPException(status_code=401, detail="Benutzer oder Passwort falsch")
    token = create_access_token(subject=user.email)
    return schemas.Token(access_token=token)


# Fester Admin-Zugang: E-Mail "admin". Es wird nur der bcrypt-Hash
# gespeichert, nicht das Passwort selbst. Wird beim Start angelegt, falls er
# noch nicht existiert (bestehende Daten werden nie ueberschrieben).
ADMIN_LOGIN_EMAIL = "admin"
ADMIN_LOGIN_HASH = "$2b$12$TOjro2lWCTCMF1gjGiqVFOziMd5NESEstrcv3.rXJ6L7WPWDeSWaa"


def ensure_admin_login() -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        exists = (
            db.query(models.User)
            .filter(func.lower(models.User.email) == ADMIN_LOGIN_EMAIL)
            .first()
        )
        if not exists:
            db.add(models.User(
                name="Admin", email=ADMIN_LOGIN_EMAIL, password_hash=ADMIN_LOGIN_HASH,
                base_role=models.BaseRole.ADMIN,
            ))
            db.commit()
    finally:
        db.close()
