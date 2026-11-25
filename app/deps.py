from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige oder abgelaufene Anmeldung",
        headers={"WWW-Authenticate": "Bearer"},
    )
    email = decode_access_token(token)
    if email is None:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.base_role != models.BaseRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Admin/Trainer")
    return user


def require_captain_or_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.base_role != models.BaseRole.ADMIN and not user.is_captain:
        raise HTTPException(status_code=403, detail="Nur für Captain oder Admin/Trainer")
    return user


def require_bussenchef(user: models.User = Depends(get_current_user)) -> models.User:
    if user.base_role != models.BaseRole.ADMIN and not user.is_bussenchef:
        raise HTTPException(status_code=403, detail="Nur für Bussenchef")
    return user


def require_harzchef(user: models.User = Depends(get_current_user)) -> models.User:
    if user.base_role != models.BaseRole.ADMIN and not user.is_harzchef:
        raise HTTPException(status_code=403, detail="Nur für Harzchef")
    return user


def require_bussenchef_or_harzchef(user: models.User = Depends(get_current_user)) -> models.User:
    if user.base_role != models.BaseRole.ADMIN and not user.is_bussenchef and not user.is_harzchef:
        raise HTTPException(status_code=403, detail="Nur für Bussenchef oder Harzchef")
    return user
