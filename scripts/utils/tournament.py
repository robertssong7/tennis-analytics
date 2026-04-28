from dataclasses import dataclass
from psycopg2.extras import execute_values


@dataclass
class Tournament:
    name: str
    level: str
    surface: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "level": self.level, "surface": self.surface}

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.name, self.surface, self.level)


ALL_TOURNAMENTS: list[Tournament] = [
    Tournament("Australian Open", "grand_slam", "hard"),
    Tournament("Roland Garros", "grand_slam", "clay"),
    Tournament("Wimbledon", "grand_slam", "grass"),
    Tournament("US Open", "grand_slam", "hard"),
    Tournament("Indian Wells Masters", "masters", "hard"),
    Tournament("Miami Open", "masters", "hard"),
    Tournament("Monte-Carlo Masters", "masters", "clay"),
    Tournament("Madrid Open", "masters", "clay"),
    Tournament("Italian Open", "masters", "clay"),
    Tournament("Canadian Open", "masters", "hard"),
    Tournament("Rogers Cup", "masters", "hard"),
    Tournament("Western & Southern Open", "masters", "hard"),
    Tournament("Cincinnati", "masters", "hard"),
    Tournament("Shanghai Masters", "masters", "hard"),
    Tournament("Paris Masters", "masters", "hard"),
    Tournament("Rolex Paris Masters", "masters", "hard"),
    Tournament("BNP Paribas Masters", "masters", "hard"),
    Tournament("Rome", "masters", "clay"),
    Tournament("Dubai Duty Free Tennis Championships", "atp_500", "hard"),
    Tournament("Qatar ExxonMobil Open", "atp_500", "hard"),
    Tournament("Abierto Mexicano Telcel", "atp_500", "hard"),
    Tournament("BB&T Atlanta Open", "atp_500", "hard"),
    Tournament("Erste Bank Open", "atp_500", "hard"),
    Tournament("Swiss Indoors Basel", "atp_500", "hard"),
    Tournament("Barcelona Open", "atp_500", "clay"),
    Tournament("Hamburg", "atp_500", "clay"),
    Tournament("Washington", "atp_500", "hard"),
    Tournament("Beijing", "atp_500", "hard"),
    Tournament("Tokyo", "atp_500", "hard"),
    Tournament("Vienna", "atp_500", "hard"),
    Tournament("Basel", "atp_500", "hard"),
    Tournament("Brisbane International", "atp_250", "hard"),
    Tournament("Sydney International", "atp_250", "hard"),
    Tournament("Auckland Open", "atp_250", "hard"),
    Tournament("Delray Beach Open", "atp_250", "hard"),
    Tournament("Winston-Salem Open", "atp_250", "hard"),
    Tournament("Geneva Open", "atp_250", "clay"),
    Tournament("Lyon Open", "atp_250", "clay"),
    Tournament("Queens Club", "atp_250", "grass"),
    Tournament("Halle Open", "atp_250", "grass"),
    Tournament("Eastbourne International", "atp_250", "grass"),
    Tournament("Stuttgart Open", "atp_250", "grass"),
    Tournament("NextGen Finals", "atp_tour_finals", "hard"),
]

ALL_TOURNAMENTS_BY_NAME: dict[str, Tournament] = {
    tournament.name: tournament for tournament in ALL_TOURNAMENTS
}

ALL_TOURNAMENTS_BY_LOWER_NAME: dict[str, Tournament] = {
    str.lower(tournament.name): tournament for tournament in ALL_TOURNAMENTS
}


def bulk_upsert_default_tournaments(conn) -> dict[str, int]:
    """
    Insert all tournaments in one statement, return {name: tournament_id}.
    tourn_map: {name: surface}
    """

    all_tournaments_as_tuples: list[tuple[str, str, str]] = [
        tournament.as_tuple() for tournament in ALL_TOURNAMENTS
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO tournaments (name, surface, level) VALUES %s ON CONFLICT (name) DO NOTHING",
            all_tournaments_as_tuples,
        )
        cur.execute(
            "SELECT name, tournament_id FROM tournaments",
        )
        return {row[0]: row[1] for row in cur.fetchall()}
