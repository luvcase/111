"""Local persistence. Provider credentials never leave the server through this API."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, directory: Path | None = None):
        if directory:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "workspace.sqlite3" if directory else ":memory:"
        self.db = sqlite3.connect(path, check_same_thread=False)
        if directory:
            os.chmod(path, 0o600)
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    owner TEXT, bucket TEXT, id TEXT, data TEXT,
                    PRIMARY KEY(owner,bucket,id));
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE, salt BLOB, hash BLOB);
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, owner TEXT, expires REAL);
            """)

    def get(self, owner, bucket, key, default=None):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM documents WHERE owner=? AND bucket=? AND id=?",
                (owner, bucket, key),
            ).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, owner, bucket):
        with self.lock:
            rows = self.db.execute(
                "SELECT data FROM documents WHERE owner=? AND bucket=?", (owner, bucket)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, owner, bucket, key, data):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO documents VALUES(?,?,?,?)",
                (owner, bucket, key, json.dumps(data, ensure_ascii=False)),
            )

    def delete(self, owner, bucket, key):
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM documents WHERE owner=? AND bucket=? AND id=?", (owner, bucket, key)
            )

    def register(self, username, password):
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        account = secrets.token_hex(16)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO accounts VALUES(?,?,?,?)", (account, username, salt, digest)
            )
        return account

    def login(self, username, password):
        with self.lock:
            row = self.db.execute(
                "SELECT id,salt,hash FROM accounts WHERE username=?", (username,)
            ).fetchone()
        salt = row[1] if row else bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return row[0] if row and hmac.compare_digest(digest, row[2]) else None

    def session(self, owner):
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.db.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 7 * 86400),
            )
        return token

    def resolve(self, token):
        with self.lock:
            row = self.db.execute(
                "SELECT owner FROM sessions WHERE digest=? AND expires>?",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return row[0] if row else None

    def revoke(self, token):
        with self.lock, self.db:
            self.db.execute(
                "DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),)
            )

    def username(self, owner):
        with self.lock:
            row = self.db.execute("SELECT username FROM accounts WHERE id=?", (owner,)).fetchone()
        return row[0] if row else "设备空间"

    def close(self):
        with self.lock:
            self.db.close()
