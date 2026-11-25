import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class BaseRole(str, enum.Enum):
    ADMIN = "admin"       # Trainer/Admin
    PLAYER = "player"     # Spieler (kann zusätzlich captain/bussenchef/harzchef sein)


class AufgebotStatus(str, enum.Enum):
    PENDING = "pending"           # mehr als 7 Tage vorher
    AUFGEBOTEN = "aufgeboten"
    VERLETZT = "verletzt"         # Bank möglich
    NICHT_AUFGEBOTEN = "nicht_aufgeboten"


class AvailabilityStatus(str, enum.Enum):
    """Spieler-Selbstauskunft (neu, unabhaengig vom Trainer-Aufgebot):
    kann der Spieler ueberhaupt antreten, oder ist er verletzt/abwesend."""
    UNBEKANNT = "unbekannt"
    KANN_SPIELEN = "kann_spielen"
    VERLETZT = "verletzt"
    ABWESEND = "abwesend"


class BussenStatus(str, enum.Enum):
    OFFEN = "offen"
    BEZAHLT = "bezahlt"


class ComplaintStatus(str, enum.Enum):
    OFFEN = "offen"
    ANGENOMMEN = "angenommen"
    ABGELEHNT = "abgelehnt"


# ---------------------------------------------------------------------------
# Users & Rollen
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    # Klartext-Kopie des zuletzt gesetzten Passworts, NUR damit der Admin es
    # einsehen kann (auf ausdruecklichen Wunsch). Fuer den Login wird
    # ausschliesslich password_hash verwendet - siehe README fuer den
    # Sicherheitshinweis dazu.
    last_plaintext_password = Column(String, nullable=True)

    base_role = Column(Enum(BaseRole), default=BaseRole.PLAYER, nullable=False)
    is_captain = Column(Boolean, default=False)
    is_bussenchef = Column(Boolean, default=False)
    is_harzchef = Column(Boolean, default=False)

    # Adresse für die Anfahrts-Berechnung
    address_street = Column(String, nullable=True)
    address_zip = Column(String, nullable=True)
    address_city = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    aufgebote = relationship("Aufgebot", back_populates="player")
    fines = relationship("Fine", foreign_keys="Fine.user_id", back_populates="player")

    @property
    def full_address(self) -> str:
        parts = [self.address_street, self.address_zip, self.address_city]
        return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Spiele (Feature 1: ICS-Import)
# ---------------------------------------------------------------------------
class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    opponent = Column(String, nullable=False)          # aus SUMMARY, Gegner (ohne eigenes Team)
    is_home = Column(Boolean, nullable=True)            # True=Heimspiel, False=Auswärts, None=nicht erkannt
    start_time = Column(DateTime, nullable=False)       # aus DTSTART
    end_time = Column(DateTime, nullable=True)           # aus DTEND (Schätzung falls fehlend)
    location_name = Column(String, nullable=True)       # Rohtext aus LOCATION
    resolved_address = Column(String, nullable=True)    # ggf. via Google Places aufgelöst
    maps_link = Column(String, nullable=True)

    ics_uid = Column(String, unique=True, nullable=True, index=True)  # Dedup über UID falls vorhanden

    created_at = Column(DateTime, default=datetime.utcnow)

    aufgebote = relationship("Aufgebot", back_populates="game", cascade="all, delete-orphan")
    availabilities = relationship("Availability", back_populates="game", cascade="all, delete-orphan")
    poll = relationship("EssenPoll", back_populates="game", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("opponent", "start_time", name="uq_game_opponent_start"),)


# ---------------------------------------------------------------------------
# Aufgebot (Feature 2)
# ---------------------------------------------------------------------------
class Aufgebot(Base):
    __tablename__ = "aufgebote"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(AufgebotStatus), default=AufgebotStatus.PENDING, nullable=False)
    set_at = Column(DateTime, nullable=True)

    game = relationship("Game", back_populates="aufgebote")
    player = relationship("User", back_populates="aufgebote")

    __table_args__ = (UniqueConstraint("game_id", "user_id", name="uq_aufgebot_game_user"),)


# ---------------------------------------------------------------------------
# Verfuegbarkeit (Spieler-Selbstauskunft, unabhaengig vom Trainer-Aufgebot)
# ---------------------------------------------------------------------------
class Availability(Base):
    __tablename__ = "availability"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(AvailabilityStatus), default=AvailabilityStatus.UNBEKANNT, nullable=False)
    set_at = Column(DateTime, default=datetime.utcnow)

    game = relationship("Game", back_populates="availabilities")
    player = relationship("User")

    __table_args__ = (UniqueConstraint("game_id", "user_id", name="uq_availability_game_user"),)


# ---------------------------------------------------------------------------
# Essen-Umfrage (Feature 4 + 5)
# ---------------------------------------------------------------------------
class EssenPoll(Base):
    __tablename__ = "essen_polls"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, unique=True)
    opens_at = Column(DateTime, nullable=False)
    closes_at = Column(DateTime, nullable=False)  # 30 Min vor Spielbeginn
    is_closed_manually = Column(Boolean, default=False)

    game = relationship("Game", back_populates="poll")
    options = relationship("PollOption", back_populates="poll", cascade="all, delete-orphan")


class PollOption(Base):
    __tablename__ = "poll_options"

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("essen_polls.id"), nullable=False)
    label = Column(String, nullable=False)  # z.B. "Pizza", "komme nicht mit"

    poll = relationship("EssenPoll", back_populates="options")
    votes = relationship("PollVote", back_populates="option", cascade="all, delete-orphan")


class PollVote(Base):
    __tablename__ = "poll_votes"

    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("essen_polls.id"), nullable=False)
    option_id = Column(Integer, ForeignKey("poll_options.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    voted_at = Column(DateTime, default=datetime.utcnow)

    option = relationship("PollOption", back_populates="votes")

    # Ein User kann pro Umfrage nur einmal abstimmen (Stimme kann geaendert werden)
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_vote_poll_user"),)


# ---------------------------------------------------------------------------
# Bussen (Feature 6) & Harz (Feature 7) - gemeinsamer Kassentopf
# ---------------------------------------------------------------------------
class Fine(Base):
    __tablename__ = "fines"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    status = Column(Enum(BussenStatus), default=BussenStatus.OFFEN, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    player = relationship("User", foreign_keys=[user_id], back_populates="fines")


class HarzExpense(Base):
    __tablename__ = "harz_expenses"

    id = Column(Integer, primary_key=True)
    amount = Column(Float, nullable=False)
    date = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_id])


# ---------------------------------------------------------------------------
# Beschwerden gegen Bussen
# ---------------------------------------------------------------------------
class Complaint(Base):
    """Beschwerde eines Spielers gegen eine eigene Busse.

    Beim Einreichen wird sofort eine Beschwerdegebuehr (fee_fine_id) als
    zusaetzliche offene Busse angelegt. Angenommen -> Busse + Gebuehr werden
    geloescht (fine_id/fee_fine_id werden dann NULL, die Felder fine_* bleiben
    als Momentaufnahme erhalten). Abgelehnt -> beides bleibt bestehen."""

    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    fine_id = Column(Integer, ForeignKey("fines.id"), nullable=True)
    fee_fine_id = Column(Integer, ForeignKey("fines.id"), nullable=True)

    # Momentaufnahme der angefochtenen Busse (bleibt auch nach dem Loeschen)
    fine_reason = Column(String, nullable=False)
    fine_amount = Column(Float, nullable=False)
    fine_date = Column(DateTime, nullable=True)
    fee_amount = Column(Float, nullable=False, default=5.0)

    message = Column(Text, nullable=False)
    status = Column(Enum(ComplaintStatus), default=ComplaintStatus.OFFEN, nullable=False)
    decision_comment = Column(Text, nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)

    player = relationship("User", foreign_keys=[user_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])
    fine = relationship("Fine", foreign_keys=[fine_id])
    fee_fine = relationship("Fine", foreign_keys=[fee_fine_id])


# ---------------------------------------------------------------------------
# App-Feedback
# ---------------------------------------------------------------------------
class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    is_done = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    player = relationship("User")
