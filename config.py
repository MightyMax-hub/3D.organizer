import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "organizer.sqlite3"
UPLOAD_DIR = DATA_DIR / "uploads"
MODEL_IMAGE_DIR = UPLOAD_DIR
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-key-before-deploying")