from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
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


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    token = create_access_token(subject=user.email)
    return schemas.Token(access_token=token)
