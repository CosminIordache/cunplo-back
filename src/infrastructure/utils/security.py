import hashlib
import hmac
import os
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, UTC

from joserfc import jwt
from joserfc.jwk import OctKey

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALG = "HS256"
JWT_TTL = timedelta(days=int(os.getenv("JWT_TTL_DAYS", "1")))

if not JWT_SECRET:
  raise RuntimeError("JWT_SECRET don't exist (Create it in the .ENV))")

_key = OctKey.import_key(JWT_SECRET)


def hash_password(password: str) -> str:
  salt = os.urandom(16)
  digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
  return f"{b64encode(salt).decode()}${b64encode(digest).decode()}"


def verify_password(password: str, hashed: str) -> bool:
  try:
    salt_b64, digest_b64 = hashed.split("$")
    salt, digest = b64decode(salt_b64), b64decode(digest_b64)
  except (ValueError, TypeError):
    return False
  candidate = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
  return hmac.compare_digest(candidate, digest)


def create_token(subject: str) -> str:
  now = datetime.now(UTC)
  claims = {
    "sub": subject,
    "iat": int(now.timestamp()),
    "exp": int((now + JWT_TTL).timestamp()),
  }
  return jwt.encode({"alg": JWT_ALG}, claims, _key)


def decode_token(token: str) -> dict:
  """Lanza JoseError si el token es inválido o ha expirado."""
  decoded = jwt.decode(token, _key, algorithms=[JWT_ALG])
  jwt.JWTClaimsRegistry(exp={"essential": True}).validate(decoded.claims)
  return decoded.claims
