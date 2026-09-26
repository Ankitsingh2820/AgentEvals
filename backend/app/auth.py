"""API-key authentication.

Keys are 256-bit random tokens ("ae_" + url-safe base64). They are stored only as SHA-256
hashes: a fast hash is appropriate here (unlike passwords) because the keys are
high-entropy random values that cannot be guessed or dictionary-attacked.

Manage keys from the command line:

    python -m app.auth create "dashboard"     # prints the key once
    python -m app.auth list
    python -m app.auth revoke <id>
"""

import hashlib
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ApiKey

KEY_PREFIX = "ae_"
LAST_USED_RESOLUTION = timedelta(minutes=1)  # avoid a DB write on every request


@dataclass(frozen=True)
class Principal:
    id: str
    name: str


ANONYMOUS = Principal("anonymous", "anonymous")


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(db: Session, name: str, key: str | None = None) -> tuple[ApiKey, str]:
    key = key or generate_key()
    record = ApiKey(name=name, key_prefix=key[:10], key_hash=hash_key(key))
    db.add(record)
    db.commit()
    return record, key


def ensure_bootstrap_key(db: Session, key: str) -> bool:
    """Register `key` if no *active* key exists (so revoking every key and setting a new
    bootstrap key recovers access). A key that was itself revoked is never re-activated.
    Returns True if the key was added."""
    if db.scalar(select(ApiKey.id).where(ApiKey.revoked_at.is_(None)).limit(1)) is not None:
        return False
    if db.scalar(select(ApiKey.id).where(ApiKey.key_hash == hash_key(key))) is not None:
        return False  # this exact key exists and was revoked: respect the revocation
    create_api_key(db, "bootstrap", key)
    return True


# Declared as FastAPI security schemes so the OpenAPI docs (/docs) show an "Authorize"
# button that sends the key with every request. auto_error=False: we produce our own 401.
_bearer = HTTPBearer(auto_error=False, description="AgentEval API key (ae_...)")
_api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Alternative to the bearer header"
)


def require_api_key(
    request: Request,
    db: Session = Depends(get_db),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
    header_key: str | None = Depends(_api_key_header),
) -> Principal:
    if not get_settings().auth_enabled:
        principal = ANONYMOUS
    else:
        key = ((bearer.credentials.strip() if bearer else None) or header_key or "").strip()
        key = key or None
        record = (
            db.scalar(
                select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None))
            )
            if key
            else None
        )
        if record is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "missing or invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        now = datetime.now(UTC)
        last = record.last_used_at
        if (
            last is None
            or now - (last if last.tzinfo else last.replace(tzinfo=UTC)) > LAST_USED_RESOLUTION
        ):
            record.last_used_at = now
            db.commit()
        principal = Principal(record.id, record.name)
    request.state.principal = principal
    return principal


def _cli(argv: list[str]) -> int:
    from app.db import SessionLocal

    usage = "usage: python -m app.auth create NAME | list | revoke ID"
    if not argv:
        print(usage)
        return 2
    with SessionLocal() as db:
        cmd = argv[0]
        if cmd == "create" and len(argv) == 2:
            record, key = create_api_key(db, argv[1])
            print(f"id:  {record.id}\nkey: {key}\nStore it now; it cannot be shown again.")
        elif cmd == "list":
            for k in db.scalars(select(ApiKey).order_by(ApiKey.created_at)):
                state = "revoked" if k.revoked_at else "active"
                print(f"{k.id}  {k.key_prefix}...  {state:8}  {k.name}  last used {k.last_used_at}")
        elif cmd == "revoke" and len(argv) == 2:
            record = db.get(ApiKey, argv[1])
            if record is None:
                print("no such key")
                return 1
            record.revoked_at = datetime.now(UTC)
            db.commit()
            print(f"revoked {record.id}")
        else:
            print(usage)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
