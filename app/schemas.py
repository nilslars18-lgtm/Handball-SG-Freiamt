from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr

from app.models import BaseRole


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserUpdateAddress(BaseModel):
    address_street: Optional[str] = None
    address_zip: Optional[str] = None
    address_city: Optional[str] = None


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    base_role: BaseRole
    is_captain: bool
    is_bussenchef: bool
    is_harzchef: bool
    address_street: Optional[str] = None
    address_zip: Optional[str] = None
    address_city: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserPasswordOut(BaseModel):
    user_id: int
    name: str
    password: Optional[str] = None
    note: Optional[str] = None


class RoleUpdate(BaseModel):
    """Nur durch Admin nutzbar, um Zusatzrollen zu vergeben."""

    is_captain: Optional[bool] = None
    is_bussenchef: Optional[bool] = None
    is_harzchef: Optional[bool] = None


class PlayerBrief(BaseModel):
    id: int
    name: str


class GameOut(BaseModel):
    id: int
    opponent: str
    is_home: Optional[bool] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    location_name: Optional[str] = None
    resolved_address: Optional[str] = None
    maps_link: Optional[str] = None

    class Config:
        from_attributes = True


class GameListItem(GameOut):
    my_aufgebot_status: str  # "pending" | "aufgeboten" | "verletzt" | "nicht_aufgeboten"
    my_aufgebot_decided: bool  # False = neutral/"Aufgebot folgt" (ausserhalb des 7-Tage-Fensters)
    my_availability_status: str = "unbekannt"  # Spieler-Selbstauskunft


class PollSummary(BaseModel):
    id: int
    status: str  # "bevorstehend" | "offen" | "geschlossen"
    opens_at: datetime
    closes_at: datetime
    my_vote_option_id: Optional[int] = None
    results_visible: bool


class GameDetail(GameListItem):
    poll: Optional[PollSummary] = None


class ImportResultItem(BaseModel):
    opponent: str
    start_time: datetime
    status: str  # "importiert" | "übersprungen (bereits vorhanden)" | "gegner nicht erkannt"


class ImportResult(BaseModel):
    imported: int
    skipped_existing: int
    unmatched_team_name: int
    details: list[ImportResultItem]


# ---------------------------------------------------------------------------
# Feature 2: Aufgebot
# ---------------------------------------------------------------------------
class AufgebotSetRequest(BaseModel):
    status: str  # "aufgeboten" | "verletzt" | "nicht_aufgeboten"


class AufgebotPlayerOut(BaseModel):
    user_id: int
    name: str
    status: str
    decided: bool
    availability_status: str = "unbekannt"  # Spieler-Selbstauskunft, als Zusatzinfo fuer den Trainer


# ---------------------------------------------------------------------------
# Verfuegbarkeit (Spieler-Selbstauskunft)
# ---------------------------------------------------------------------------
class AvailabilitySetRequest(BaseModel):
    status: str  # "kann_spielen" | "verletzt" | "abwesend"


class AvailabilityOut(BaseModel):
    status: str
    set_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Feature 3: Anfahrt
# ---------------------------------------------------------------------------
class DepartureInfo(BaseModel):
    origin: str
    destination: str
    arrival_target: datetime
    departure_time: Optional[datetime] = None
    duration_text: Optional[str] = None
    distance_text: Optional[str] = None
    note: Optional[str] = None  # z.B. Hinweis falls kein API-Key gesetzt


# ---------------------------------------------------------------------------
# Feature 4: Essen-Umfrage
# ---------------------------------------------------------------------------
class PollOptionOut(BaseModel):
    id: int
    label: str


class PollVoteRequest(BaseModel):
    option_id: int


class PollVoteOut(BaseModel):
    status: str  # "offen"
    my_vote_option_id: int
    message: str = "Stimme gespeichert. Ergebnisse werden erst nach dem Spiel sichtbar."


class PollOptionResult(BaseModel):
    option_id: int
    label: str
    votes: int
    percentage: float


class PollResultsOut(BaseModel):
    status: str
    total_votes: int
    results: list[PollOptionResult]
    mitkommende: list[str]  # Namen aller ausser "komme nicht mit"-Waehler
    winning_options: list[str]  # >1 bei echtem Gleichstand


# ---------------------------------------------------------------------------
# Feature 5: Restaurantvorschläge
# ---------------------------------------------------------------------------
class RestaurantSuggestion(BaseModel):
    name: str
    address: Optional[str] = None
    maps_link: str


class RestaurantSuggestionsOut(BaseModel):
    based_on_options: list[str]
    suggestions: list[RestaurantSuggestion]
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Feature 6: Bussenliste
# ---------------------------------------------------------------------------
class FineCreate(BaseModel):
    user_id: int
    reason: str
    amount: float


class FineUpdate(BaseModel):
    reason: Optional[str] = None
    amount: Optional[float] = None


class FineOut(BaseModel):
    id: int
    user_id: int
    player_name: str
    reason: str
    amount: float
    date: datetime
    status: str  # "offen" | "bezahlt"
    # Nur bei /fines/me und /fines/player/{id} befuellt:
    is_complaint_fee: bool = False          # ist selbst eine Beschwerdegebuehr
    complaint_id: Optional[int] = None      # Beschwerde zu dieser Busse (falls vorhanden)
    complaint_status: Optional[str] = None  # "offen" | "abgelehnt" (angenommene Busse existiert nicht mehr)

    class Config:
        from_attributes = True


class FineOverviewOut(BaseModel):
    total_offen: float
    total_bezahlt: float
    kassenstand: float  # total_bezahlt - Summe aller Harz-Ausgaben


class PlayerFineSummary(BaseModel):
    user_id: int
    name: str
    total_offen: float
    total_bezahlt: float


# ---------------------------------------------------------------------------
# Feature 7: Harz-Ausgaben
# ---------------------------------------------------------------------------
class HarzExpenseCreate(BaseModel):
    amount: float
    note: Optional[str] = None


class HarzExpenseOut(BaseModel):
    id: int
    amount: float
    date: datetime
    note: Optional[str] = None
    created_by_name: str

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Startseite
# ---------------------------------------------------------------------------
class NextGameOut(BaseModel):
    game: Optional[GameDetail] = None


# ---------------------------------------------------------------------------
# Einstellungen: Benachrichtigungen
# ---------------------------------------------------------------------------
class NotificationCount(BaseModel):
    polls_open: int
    fines_open: int
    complaints_open: int = 0  # nur fuer Bussenchef/Admin: zu bearbeitende Beschwerden
    total: int


# ---------------------------------------------------------------------------
# Beschwerden gegen Bussen
# ---------------------------------------------------------------------------
class ComplaintCreate(BaseModel):
    fine_id: int
    message: str


class ComplaintDecision(BaseModel):
    decision: str  # "annehmen" | "ablehnen"
    comment: Optional[str] = None


class ComplaintComment(BaseModel):
    comment: str


class ComplaintOut(BaseModel):
    id: int
    user_id: int
    player_name: str
    fine_id: Optional[int] = None  # None, sobald die Busse (nach Annahme) geloescht wurde
    fine_reason: str
    fine_amount: float
    fine_date: Optional[datetime] = None
    fee_amount: float
    message: str
    status: str  # "offen" | "angenommen" | "abgelehnt"
    decision_comment: Optional[str] = None
    decided_by_name: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# App-Feedback
# ---------------------------------------------------------------------------
class FeedbackCreate(BaseModel):
    category: str
    message: str


class FeedbackOut(BaseModel):
    id: int
    user_id: int
    player_name: str
    category: str
    message: str
    is_done: bool
    created_at: datetime
