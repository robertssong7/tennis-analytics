"""
TennisIQ API Configuration
Reads settings from environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
_default_origins = "http://localhost:3001,https://tennisiq-one.vercel.app,https://tennisiq.vercel.app"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", _default_origins).split(",")
    if origin.strip()
]
