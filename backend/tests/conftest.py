"""Shared test setup. Env vars are set before any ``app`` import reads settings."""

import os

from cryptography.fernet import Fernet

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
