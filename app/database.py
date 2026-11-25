import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
if settings.database_url.startswith("sqlite"):
    engine = create_engine(settings.database_url, connect_args=connect_args)
elif os.environ.get("VERCEL"):
    # Serverless: jede Anfrage kann in einer frischen Instanz laufen, daher keine
    # eigenen Verbindungs-Pools halten (den Pool uebernimmt Neon "-pooler").
    engine = create_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
