"""Cifrado simétrico de los tokens de terceros que guardamos en Mongo."""
import os
from typing import Optional

from cryptography.fernet import Fernet

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not ENCRYPTION_KEY:
  raise RuntimeError(
    "ENCRYPTION_KEY don't exist (create in the .ENV)"
  )

_fernet = Fernet(ENCRYPTION_KEY)


def encrypt(value: Optional[str]) -> Optional[str]:
  return _fernet.encrypt(value.encode()).decode() if value else None


def decrypt(value: Optional[str]) -> Optional[str]:
  return _fernet.decrypt(value.encode()).decode() if value else None
