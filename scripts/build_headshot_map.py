#!/usr/bin/env python3
"""Build data/player_headshots.json mapping player names to ATP headshot URLs."""
import json
from pathlib import Path

PLAYER_IDS = {
    "Jannik Sinner": "s0ag",
    "Carlos Alcaraz": "a0e2",
    "Novak Djokovic": "d643",
    "Alexander Zverev": "z355",
    "Daniil Medvedev": "me51",
    "Andrey Rublev": "re44",
    "Casper Ruud": "rh16",
    "Hubert Hurkacz": "hb71",
    "Holger Rune": "r0dg",
    "Taylor Fritz": "fb98",
    "Stefanos Tsitsipas": "te51",
    "Tommy Paul": "ph85",
    "Alex De Minaur": "dh58",
    "Ben Shelton": "su87",
    "Grigor Dimitrov": "d875",
    "Ugo Humbert": "hf20",
    "Frances Tiafoe": "th81",
    "Felix Auger-Aliassime": "ag37",
    "Sebastian Korda": "k0ah",
    "Lorenzo Musetti": "m0ej",
    "Karen Khachanov": "k09f",
    "Nicolas Jarry": "j0bh",
    "Alejandro Tabilo": "t0ew",
    "Jack Draper": "d0co",
    "Arthur Fils": "f0ex",
    "Tomas Machac": "m0fm",
    "Sebastian Baez": "b0bf",
    "Tallon Griekspoor": "gk51",
    "Jan-Lennard Struff": "s0i0",
    "Matteo Berrettini": "bk40",
    "Rafael Nadal": "n409",
    "Roger Federer": "f324",
    "Andy Murray": "mc10",
}

BASE_URL = "https://www.atptour.com/en/-/media/alias/player-headshot/{}"

headshots = {name: BASE_URL.format(pid) for name, pid in PLAYER_IDS.items()}

out = Path("data/player_headshots.json")
out.write_text(json.dumps(headshots, indent=2))
print(f"Saved {len(headshots)} headshots to {out}")
print("Sinner URL:", headshots["Jannik Sinner"])
