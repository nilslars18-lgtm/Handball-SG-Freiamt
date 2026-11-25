from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    secret_key: str = "change-me-to-a-long-random-string"
    database_url: str = "sqlite:///./handball.db"
    google_maps_api_key: str = ""
    access_token_expire_minutes: int = 10080  # 7 Tage
    team_name: str = "SG Freiamt PLUS"
    complaint_fee: float = 5.0  # Gebuehr (CHF) fuer jede eingereichte Busse-Beschwerde

    # Feature 2: Aufgebot
    aufgebot_days_before: int = 7  # ab wie vielen Tagen vor dem Spiel darf der Trainer aufbieten

    # Feature 3: Anfahrt
    arrival_buffer_minutes: int = 120  # 2 Stunden vor Spielbeginn in der Halle sein

    # Feature 4: Essen-Umfrage
    poll_open_weekday: int = 3          # 0=Montag ... 3=Donnerstag
    poll_open_hour: int = 14
    poll_open_minute: int = 0
    poll_close_minutes_before_game: int = 30
    poll_default_options: str = "Pizza,Asiatisch,Burger,Kebab,Sonstiges,komme nicht mit"
    poll_skip_option: str = "komme nicht mit"  # diese Option zaehlt nicht zu "kommt mit"
    game_default_duration_minutes: int = 90  # falls DTEND in der ICS fehlt

    class Config:
        env_file = ".env"

    # Manche Hoster (u.a. Neon, Render, Railway) geben die Postgres-URL noch
    # im alten "postgres://"-Format raus - SQLAlchemy 2.x akzeptiert nur
    # "postgresql://", deshalb hier automatisch korrigieren.
    @field_validator("database_url")
    @classmethod
    def _fix_postgres_scheme(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v

    @property
    def poll_default_options_list(self) -> list[str]:
        return [o.strip() for o in self.poll_default_options.split(",") if o.strip()]


settings = Settings()
