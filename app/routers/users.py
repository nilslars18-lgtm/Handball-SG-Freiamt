from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.security import hash_password, verify_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=schemas.UserOut)
def read_own_profile(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.put("/me/address", response_model=schemas.UserOut)
def update_own_address(
    address: schemas.UserUpdateAddress,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    for field, value in address.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/password")
def change_own_password(
    body: schemas.PasswordChange,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Jeder eingeloggte User kann sein eigenes Passwort aendern. Auf
    ausdruecklichen Wunsch wird zusaetzlich eine Klartext-Kopie gespeichert,
    damit der Admin das neue Passwort einsehen kann (siehe /users/{id}/password)."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    if len(body.new_password) < 4:
        raise HTTPException(status_code=400, detail="Neues Passwort ist zu kurz")
    current_user.password_hash = hash_password(body.new_password)
    current_user.last_plaintext_password = body.new_password
    db.commit()
    return {"message": "Passwort geaendert"}


@router.get("/{user_id}/password", response_model=schemas.UserPasswordOut)
def view_user_password(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Admin-only: zeigt das zuletzt gesetzte Passwort im Klartext an.
    Sicherheitshinweis: Dies ist nur moeglich, weil beim Setzen/Aendern des
    Passworts zusaetzlich eine Klartext-Kopie abgelegt wird - fuer den Login
    selbst wird immer nur der bcrypt-Hash verwendet. Diese Funktion wurde auf
    ausdruecklichen Wunsch eingebaut, ist aber ein Abstrich bei der
    Passwortsicherheit (siehe README)."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    return schemas.UserPasswordOut(
        user_id=user.id,
        name=user.name,
        password=user.last_plaintext_password,
        note=None if user.last_plaintext_password else "Kein Klartext-Passwort gespeichert (z.B. sehr alter Account).",
    )


@router.delete("/{user_id}")
def delete_player(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Admin-only: entfernt einen Spieler-Account (deaktiviert ihn - Historie
    wie Bussen/Aufgebote bleibt erhalten). Admin-Accounts koennen hier nicht
    geloescht werden."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.base_role == models.BaseRole.ADMIN:
        raise HTTPException(status_code=400, detail="Admin-Accounts koennen nicht geloescht werden")
    user.is_active = False
    db.commit()
    return {"message": "Spieler entfernt"}


@router.get("/players", response_model=List[schemas.PlayerBrief])
def list_players_brief(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Leichte Namensliste aller Spieler (ohne sensible Felder) - fuer
    Dropdowns bei Bussen/Aufgebot, fuer jeden eingeloggten User sichtbar."""
    players = db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712
    return [schemas.PlayerBrief(id=p.id, name=p.name) for p in players]


@router.get("", response_model=List[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db), admin: models.User = Depends(require_admin)
):
    return db.query(models.User).filter(models.User.is_active == True).all()  # noqa: E712


@router.patch("/{user_id}/roles", response_model=schemas.UserOut)
def update_roles(
    user_id: int,
    role_update: schemas.RoleUpdate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    for field, value in role_update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user
