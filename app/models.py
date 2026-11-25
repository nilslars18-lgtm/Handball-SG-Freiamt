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
    LargeBinary,
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
    staff = relationship("StaffRole", uselist=False, viewonly=True, lazy="selectin")

    @property
    def staff_title(self):
        return self.staff.title if self.staff else None

    @property
    def is_trainer(self) -> bool:
        """Admin oder Trainer/Staff: darf Aufgebot, Termine usw. verwalten."""
        return self.base_role == BaseRole.ADMIN or self.staff is not None

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
# Dokumente (Ordnerstruktur) - Dateien liegen als Bytes direkt in der DB,
# weil Render (Free) hochgeladene Dateien beim Einschlafen loescht.
# ---------------------------------------------------------------------------
class DocFolder(Base):
    __tablename__ = "doc_folders"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("doc_folders.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DocItem(Base):
    __tablename__ = "doc_items"

    id = Column(Integer, primary_key=True)
    folder_id = Column(Integer, ForeignKey("doc_folders.id"), nullable=False)
    title = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="file")  # "file" | "info"
    filename = Column(String, nullable=True)
    mime = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    data = Column(LargeBinary, nullable=True)
    text = Column(Text, nullable=True)  # fuer Info-Eintraege
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Fotos & Videos (nur Links, pro Spiel)
# ---------------------------------------------------------------------------
class MediaLink(Base):
    __tablename__ = "media_links"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=True)
    url = Column(String, nullable=False)
    title = Column(String, nullable=True)
    kind = Column(String, nullable=False, default="foto")  # "foto" | "video"
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    game = relationship("Game")
    created_by = relationship("User")


# ---------------------------------------------------------------------------
# Spielbericht: wer schreibt (Rotation) + vorbereitete Daten
# ---------------------------------------------------------------------------
class GameReport(Base):
    __tablename__ = "game_reports"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, unique=True)
    writer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    writer_assigned_at = Column(DateTime, nullable=True)
    writer_manual = Column(Boolean, default=False)  # vom Trainer fix gesetzt

    score_home = Column(Integer, nullable=True)       # Tore SG Freiamt PLUS
    score_away = Column(Integer, nullable=True)       # Tore Gegner
    half_home = Column(Integer, nullable=True)
    half_away = Column(Integer, nullable=True)

    team_photo = Column(LargeBinary, nullable=True)
    stats_image = Column(LargeBinary, nullable=True)
    stats_json = Column(Text, nullable=True)  # [{name, tore, siebenm, zwei_min, gelb}]
    text = Column(Text, nullable=True)        # der eigentliche Bericht
    is_done = Column(Boolean, default=False)

    updated_at = Column(DateTime, default=datetime.utcnow)

    game = relationship("Game")
    writer = relationship("User")


# ---------------------------------------------------------------------------
# Bussen-Beschwerde: Spieler ficht offene Busse an -> sofort CHF 5 Gebuehr.
# Bussenchef/Admin: annehmen (Busse + Gebuehr weg) oder ablehnen (beides bleibt).
# Sichtbar nur fuer Beschwerdefuehrer, Bussenchef und Admin.
# ---------------------------------------------------------------------------
class FineComplaint(Base):
    __tablename__ = "fine_complaints"

    id = Column(Integer, primary_key=True)
    fine_id = Column(Integer, ForeignKey("fines.id"), nullable=True)      # None nach Annahme (Busse geloescht)
    fee_fine_id = Column(Integer, ForeignKey("fines.id"), nullable=True)  # die CHF-5-Gebuehr
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="offen")  # offen | angenommen | abgelehnt
    response_comment = Column(Text, nullable=True)
    # Kopie der Busse, damit die Historie nach dem Loeschen lesbar bleibt
    fine_reason = Column(String, nullable=True)
    fine_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])


# ---------------------------------------------------------------------------
# Feedback (fuer alle, Auswertung durch Admin)
# ---------------------------------------------------------------------------
class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(String, nullable=False)  # fehler | idee | lob | sonstiges
    text = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


# ---------------------------------------------------------------------------
# App-Dateien wie das Vereinslogo (in der DB, damit es Render-Neustarts ueberlebt)
# ---------------------------------------------------------------------------
class AppAsset(Base):
    __tablename__ = "app_assets"

    key = Column(String, primary_key=True)  # z.B. "logo"
    data = Column(LargeBinary, nullable=False)
    mime = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Trainings, Events & Teamsitzungen
#   kind: "training" (gruen), "event" (blau), "sitzung" (blau)
#   Standard-Trainings entstehen aus einer TrainingSeries (jede Woche an den
#   gewaehlten Wochentagen), Spezialtrainings/Events sind Einzeltermine.
#   Zeiten sind wie bei den Spielen Schweizer Lokalzeit (ohne Zeitzone).
# ---------------------------------------------------------------------------
class TrainingSeries(Base):
    __tablename__ = "training_series"

    id = Column(Integer, primary_key=True)
    weekdays = Column(String, nullable=False)       # z.B. "1,3" (0=Mo ... 6=So)
    start_hhmm = Column(String, nullable=False)     # "19:00"
    duration_minutes = Column(Integer, default=90)
    location = Column(String, nullable=True)
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class TeamEvent(Base):
    __tablename__ = "team_events"

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False, default="training")
    title = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    location = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    series_id = Column(Integer, ForeignKey("training_series.id"), nullable=True)
    is_cancelled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    responses = relationship("EventResponse", back_populates="event", cascade="all, delete-orphan")


class EventResponse(Base):
    __tablename__ = "event_responses"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("team_events.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=True)      # "zusage" | "absage" | None
    reason = Column(String, nullable=True)
    present = Column(Boolean, nullable=True)    # vom Trainer nach dem Training gesetzt
    set_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("TeamEvent", back_populates="responses")
    user = relationship("User")

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_response"),)


# ---------------------------------------------------------------------------
# In-App-Hinweise (z.B. Erinnerung "bitte eintragen")
# ---------------------------------------------------------------------------
class Notice(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    text = Column(String, nullable=False)
    ref_kind = Column(String, nullable=True)   # "game" | "event"
    ref_id = Column(Integer, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Aemtli (Baelle, Trikots, Zeitnehmer ...) - reihum verteilt
# ---------------------------------------------------------------------------
class Duty(Base):
    __tablename__ = "duties"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    applies_to = Column(String, nullable=False, default="spiel")  # "spiel" | "training" | "beide"
    is_active = Column(Boolean, default=True)


class DutyAssignment(Base):
    __tablename__ = "duty_assignments"

    id = Column(Integer, primary_key=True)
    duty_id = Column(Integer, ForeignKey("duties.id"), nullable=False)
    ref_kind = Column(String, nullable=False)  # "game" | "event"
    ref_id = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    manual = Column(Boolean, default=False)

    duty = relationship("Duty")
    user = relationship("User")

    __table_args__ = (UniqueConstraint("duty_id", "ref_kind", "ref_id", name="uq_duty_ref"),)


# ---------------------------------------------------------------------------
# Von handball.ch geholte Spieldaten (Statistik + Liveticker) pro Spiel
# ---------------------------------------------------------------------------
class HbGameData(Base):
    __tablename__ = "hb_game_data"

    game_id = Column(Integer, ForeignKey("games.id"), primary_key=True)
    hb_game_id = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)   # JSON: Resultat, Spieler, Torhueter, Liveticker
    draft = Column(Text, nullable=True)      # automatischer Entwurf
    imported_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Protokoll aller An-/Abmeldungen (Spiele + Termine) - fuer den Admin
# ---------------------------------------------------------------------------
class ResponseLog(Base):
    __tablename__ = "response_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ref_kind = Column(String, nullable=False)   # "game" | "event"
    ref_id = Column(Integer, nullable=False, index=True)
    status = Column(String, nullable=False)     # kann_spielen/verletzt/abwesend | zusage/absage/verletzt/individuell
    previous = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


# ---------------------------------------------------------------------------
# Bussenkatalog (vordefinierte Bussen)
# ---------------------------------------------------------------------------
class FineType(Base):
    __tablename__ = "fine_types"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    category = Column(String, nullable=True)   # z.B. "Training", "Spiel", "Allgemein"
    note = Column(String, nullable=True)
    sort = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)


# ---------------------------------------------------------------------------
# Push-Benachrichtigungen + Kalender-Abo
# ---------------------------------------------------------------------------
class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_ok_at = Column(DateTime, nullable=True)


class NotifyPref(Base):
    __tablename__ = "notify_prefs"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    key = Column(String, primary_key=True)   # z.B. "aufgebot", "bussen"
    enabled = Column(Boolean, default=True)


class PushSent(Base):
    """Merkt sich automatische Meldungen, damit jede nur einmal kommt."""
    __tablename__ = "push_sent"

    key = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CalendarToken(Base):
    __tablename__ = "calendar_tokens"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    token = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class StaffRole(Base):
    """Trainer / TW-Trainer: haben ein Login und Trainer-Rechte, zaehlen aber nicht
    als Spieler (kein Aufgebot, keine Ämtli, keine Spielbericht-Rotation)."""
    __tablename__ = "staff_roles"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    title = Column(String, nullable=False, default="Trainer")
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Chat (wie WhatsApp): Team-Info (nur Trainer schreiben), Spieler-Chat, Direktnachrichten
# ---------------------------------------------------------------------------
class ChatRoom(Base):
    __tablename__ = "chat_rooms"

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)          # "team" | "players" | "dm"
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatMember(Base):
    """Nur fuer Direktnachrichten (Gruppen-Mitglieder ergeben sich aus den Rollen)."""
    __tablename__ = "chat_members"

    room_id = Column(Integer, ForeignKey("chat_rooms.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)


class ChatRead(Base):
    __tablename__ = "chat_reads"

    room_id = Column(Integer, ForeignKey("chat_rooms.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    last_read_id = Column(Integer, default=0)
    muted = Column(Boolean, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, ForeignKey("chat_rooms.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    kind = Column(String, default="text")          # "text" | "poll"
    text = Column(Text, nullable=True)
    poll_multiple = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class ChatPollOption(Base):
    __tablename__ = "chat_poll_options"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=False, index=True)
    text = Column(String, nullable=False)
    sort = Column(Integer, default=0)


class ChatPollVote(Base):
    __tablename__ = "chat_poll_votes"

    option_id = Column(Integer, ForeignKey("chat_poll_options.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=False, index=True)
    voted_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Chat-Erweiterungen wie WhatsApp (eigene Tabellen, da create_all keine
# Spalten zu bestehenden Tabellen hinzufuegt)
# ---------------------------------------------------------------------------
class ChatMsgExtra(Base):
    """Antwort auf eine andere Nachricht."""
    __tablename__ = "chat_msg_extra"

    message_id = Column(Integer, ForeignKey("chat_messages.id"), primary_key=True)
    reply_to_id = Column(Integer, nullable=True)


class ChatImage(Base):
    __tablename__ = "chat_images"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True, index=True)
    data = Column(LargeBinary, nullable=False)
    mime = Column(String, default="image/jpeg")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatCursor(Base):
    """Bis zu welcher Nachricht ist ein Raum bei einer Person zugestellt (App hat sie geladen)."""
    __tablename__ = "chat_cursors"

    room_id = Column(Integer, ForeignKey("chat_rooms.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    delivered_id = Column(Integer, default=0)


class ChatCursorLog(Base):
    """Zeitpunkte fuer 'Zugestellt' (d) und 'Gelesen' (r) - fuer die Nachrichten-Info."""
    __tablename__ = "chat_cursor_log"

    id = Column(Integer, primary_key=True)
    room_id = Column(Integer, index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    kind = Column(String(1), nullable=False)
    upto_id = Column(Integer, nullable=False)
    at = Column(DateTime, default=datetime.utcnow)


class ChatPresence(Base):
    __tablename__ = "chat_presence"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    last_seen = Column(DateTime, nullable=True)


class ChatPrivacy(Base):
    """Wie bei WhatsApp: 'Zuletzt online' und 'Lesebestaetigungen' (gilt gegenseitig)."""
    __tablename__ = "chat_privacy"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    show_last_seen = Column(Boolean, default=True)
    read_receipts = Column(Boolean, default=True)


class UserAvatar(Base):
    __tablename__ = "user_avatars"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    data = Column(LargeBinary, nullable=False)
    mime = Column(String, default="image/jpeg")
    updated_at = Column(DateTime, default=datetime.utcnow)


class PlayerRole(Base):
    """Torhueter von Hand festlegen (ueberschreibt die automatische Erkennung von handball.ch)."""
    __tablename__ = "player_roles"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    goalie = Column(Boolean, nullable=True)
