from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.deps import get_current_user, require_admin, require_owner
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
    """Jeder eingeloggte User kann sein eigenes Passwort aendern. Ein selbst
    gewaehltes Passwort wird NICHT im Klartext gespeichert (Datenschutz) -
    der Admin sieht dann nur noch, dass es selbst geaendert wurde."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    if len(body.new_password) < 4:
        raise HTTPException(status_code=400, detail="Neues Passwort ist zu kurz")
    current_user.password_hash = hash_password(body.new_password)
    current_user.last_plaintext_password = None
    db.commit()
    return {"message": "Passwort geaendert"}


@router.get("/{user_id}/password", response_model=schemas.UserPasswordOut)
def view_user_password(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_owner),
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
        note=None if user.last_plaintext_password else "Der Spieler hat sein Passwort selbst geändert – es ist nicht lesbar gespeichert. Bei Bedarf ein neues setzen.",
    )


@router.delete("/{user_id}")
def delete_player(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_owner),
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
    players = (
        db.query(models.User)
        .filter(models.User.is_active == True, models.User.email != "admin")  # noqa: E712
        .all()
    )  # der technische "admin"-Login ist kein Spieler
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
    admin: models.User = Depends(require_owner),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    for field, value in role_update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), admin: models.User = Depends(require_owner)):
    """Admin: neues Passwort generieren (z.B. wenn ein Spieler seins vergessen hat)."""
    from app.routers.handballch import make_password
    from app.security import hash_password
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.base_role == models.BaseRole.ADMIN:
        raise HTTPException(status_code=400, detail="Admin-Passwörter hier nicht zurücksetzen")
    pw = make_password()
    user.password_hash = hash_password(pw)
    user.last_plaintext_password = pw
    db.commit()
    return {"login": user.email, "password": pw}


class StaffIn(BaseModel):
    title: Optional[str] = None   # leer = kein Trainer (wieder Spieler)


@router.put("/{user_id}/staff", response_model=schemas.UserOut)
def set_staff(user_id: int, body: StaffIn, db: Session = Depends(get_db), owner: models.User = Depends(require_owner)):
    """Trainer/TW-Trainer markieren: Trainer-Rechte, zaehlt nicht mehr als Spieler."""
    user = db.get(models.User, user_id)
    if not user or user.base_role == models.BaseRole.ADMIN:
        raise HTTPException(404, "Spieler nicht gefunden")
    row = db.get(models.StaffRole, user_id)
    title = (body.title or "").strip()
    if title:
        if row is None:
            db.add(models.StaffRole(user_id=user_id, title=title[:40]))
        else:
            row.title = title[:40]
    elif row is not None:
        db.delete(row)
    db.commit()
    db.expire(user)
    return db.get(models.User, user_id)
