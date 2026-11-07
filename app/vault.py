"""Encrypted local credential storage. API callers only receive entry metadata."""
import json
import os
import threading
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from . import store

_key_lock = threading.Lock()


def origin(url):
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        port = parsed.port
        hostname = parsed.hostname.lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        suffix = f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
        return f"{parsed.scheme}://{hostname}{suffix}"
    except ValueError:
        raise HTTPException(422, "Use a valid http(s) website URL without embedded credentials.")


def cipher():
    key = os.environ.get("HUMBLE_VAULT_KEY")
    if not key:
        with _key_lock:
            store.DATA.mkdir(parents=True, exist_ok=True)
            path = store.DATA / "vault.key"
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
            key = path.read_bytes()
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise HTTPException(503, "The vault encryption key is unavailable or invalid. Check the local vault setup.")


def encrypt(user_id, credentials):
    return cipher().encrypt(json.dumps({"owner": user_id, "credentials": credentials}).encode())


def decrypt(user_id, entry):
    try:
        payload = json.loads(cipher().decrypt(entry["secret"]))
        if payload["owner"] != user_id:
            raise InvalidToken()
        return payload["credentials"]
    except (InvalidToken, KeyError, ValueError):
        raise HTTPException(503, "This vault entry cannot be unlocked. Restore its original vault key or replace the entry.")


def metadata(entry):
    return {k: entry[k] for k in ("id", "name", "origin", "has_login", "header_count", "created", "updated")}


def owned(entry_id, user_id):
    with store.connect() as db:
        entry = db.execute("SELECT * FROM vault_entries WHERE id=? AND user_id=?", (entry_id, user_id)).fetchone()
    if entry is None:
        raise HTTPException(404, "Vault entry not found.")
    return entry
