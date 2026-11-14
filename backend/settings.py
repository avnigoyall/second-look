"""Environment and shared config. Every secret comes from the .env file in the project root."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MONGODB_URI = os.getenv("MONGODB_URI", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
DB_NAME = "secondlook"

ATTRIBUTES = json.loads((Path(__file__).parent / "config" / "attributes.json").read_text())
