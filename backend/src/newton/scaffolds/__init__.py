"""Scaffold library — proven, parameterized file templates for the Build engine.

The templates-first half of enterprise Build: instead of asking a small local model to *invent*
a session manager, an error middleware, or a CI workflow (which it does unreliably), Newton lays
these down deterministically from templates that are known-correct, and asks the model only to
fill the domain-specific gaps (your models' fields, your routes' logic).

A template is `complete` (ready as-is → the model is skipped for that file) or a skeleton the
model completes (a working starting point, so it edits rather than starts from a blank file).
Placeholders are substituted by simple token replacement (not str.format, so the code's own
braces are left alone):
  __PKG__      root package prefix, dotted with a trailing dot, or "" (e.g. "app." or "")
  __APP_NAME__ a human title for the app
  __MAINMOD__  the app entrypoint module path (e.g. "app.main" or "main")
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Scaffold:
    text: str
    complete: bool          # True = ready as-is (skip the model); False = skeleton to complete
    note: str = ""          # guidance appended to the task goal when complete is False


# --- templates (FastAPI + SQLite/SQLAlchemy + pytest + GitHub Actions) ----------

_CONFIG = '''"""Application configuration — environment-driven settings."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    app_name: str = "__APP_NAME__"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    secret_key: str = os.getenv("SECRET_KEY", "change-me-in-production")
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL", "86400"))
    debug: bool = os.getenv("DEBUG", "0") == "1"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
'''

_DB = '''"""Database engine, session factory, and declarative base (SQLAlchemy 2.0)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from __PKG__config import get_settings

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base all models inherit from."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Import your models before calling so they are registered."""
    Base.metadata.create_all(bind=engine)
'''

_ERRORS = '''"""Uniform error handling — domain exceptions and their FastAPI handlers."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for expected application errors (turned into a clean JSON response)."""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404


class AuthError(AppError):
    status_code = 401


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so every error becomes a consistent JSON body."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": "internal server error"})
'''

_SESSIONS = '''"""Signed-token session management (standard library only — no extra dependency)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from __PKG__config import get_settings


def create_session(user_id: int | str) -> str:
    """Return a tamper-proof session token carrying the user id and issue time."""
    s = get_settings()
    payload = json.dumps({"uid": user_id, "iat": int(time.time())}).encode()
    sig = hmac.new(s.secret_key.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode()


def read_session(token: str) -> dict | None:
    """Validate a session token; return its payload, or None if invalid or expired."""
    s = get_settings()
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload, sig = raw.rsplit(b".", 1)
        expected = hmac.new(s.secret_key.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(payload)
        if int(time.time()) - int(data.get("iat", 0)) > s.session_ttl_seconds:
            return None
        return data
    except Exception:
        return None
'''

_AUTH = '''"""Authentication helpers — password hashing (ready) and user auth (to complete).

Scaffolded: password hashing/verification is production-ready (stdlib PBKDF2). Complete
register_user / authenticate_user against your User model and a database session.
"""
from __future__ import annotations

import hashlib
import hmac
import os


def hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256 hash, stored as "salt$hash"."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored "salt$hash"."""
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# TODO(complete): implement register_user(db, ...) and authenticate_user(db, ...) using the
# User model and the hashing helpers above; raise AuthError on bad credentials.
'''

_MODELS = '''"""SQLAlchemy models. Scaffolded: Base is imported and a User stub is provided —
add the domain models the goal requires and flesh out their fields."""
from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from __PKG__db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str] = mapped_column()


# TODO(complete): add the domain models the project goal calls for.
'''

_MAIN = '''"""FastAPI application entrypoint. Scaffolded: the app, error handlers, and table
creation are wired — include your routers where indicated."""
from __future__ import annotations

from fastapi import FastAPI

from __PKG__db import init_db
from __PKG__errors import register_error_handlers

app = FastAPI(title="__APP_NAME__")
register_error_handlers(app)


@app.on_event("startup")
def _on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# TODO(complete): include your routers, e.g.
#   from __PKG__routers import tasks
#   app.include_router(tasks.router)
'''

_ROUTES = '''"""API routes. Scaffolded: an APIRouter and the DB dependency are ready — add endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from __PKG__db import get_db

router = APIRouter()


@router.get("/ping")
def ping() -> dict:
    return {"pong": True}


# TODO(complete): add the endpoints the goal requires, using Depends(get_db) for the session.
'''

_TESTS = '''"""Tests. Scaffolded: a TestClient is ready against the app — add real assertions."""
from __future__ import annotations

from fastapi.testclient import TestClient

from __MAINMOD__ import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").status_code == 200


# TODO(complete): add tests for the endpoints and logic the goal requires.
'''

_REQUIREMENTS = """fastapi
uvicorn[standard]
sqlalchemy>=2.0
pytest
httpx
"""

_DOCKERFILE = '''FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "__MAINMOD__:app", "--host", "0.0.0.0", "--port", "8000"]
'''

_CI = '''name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: pytest -q
'''


# --- React + Vite + TypeScript frontend stack (pairs with the FastAPI backend) ---

_PKG_JSON = '''{
  "name": "__APP_SLUG__-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.3",
    "vite": "^5.4.0"
  }
}
'''

_INDEX_HTML = '''<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>__APP_NAME__</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
'''

_VITE_CONFIG = '''import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend, stripping the /api prefix, so the
// frontend can call the backend's routes directly during development.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\\/api/, "") },
    },
  },
});
'''

_TSCONFIG = '''{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
'''

_MAIN_TSX = '''import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
'''

_API_TS = '''// Typed client for the backend. Scaffolded: a fetch helper is ready — add the calls the UI needs.
const BASE = "/api";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// TODO(complete): add the calls the UI needs, e.g.
//   export const listTasks = () => api<Task[]>("/tasks");
'''

_APP_TSX = '''// Scaffolded: a minimal shell that checks the backend health — build the UI the goal needs.
import { useEffect, useState } from "react";
import { api } from "./api";

export default function App() {
  const [status, setStatus] = useState("…");
  useEffect(() => {
    api<{ status: string }>("/health")
      .then((r) => setStatus(r.status))
      .catch(() => setStatus("offline"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 32 }}>
      <h1>__APP_NAME__</h1>
      <p>Backend status: {status}</p>
      {/* TODO(complete): build the interface the goal requires. */}
    </main>
  );
}
'''

# Frontend files that are complete boilerplate vs. skeletons the model fills.
_FRONTEND_COMPLETE = {
    "package.json": _PKG_JSON,
    "index.html": _INDEX_HTML,
    "vite.config.ts": _VITE_CONFIG,
    "tsconfig.json": _TSCONFIG,
}


def _slug(text: str) -> str:
    s = re.sub(r"[^\w-]+", "-", text.lower()).strip("-")
    return s or "app"


def _is_frontend_file(path: str) -> bool:
    """A file that belongs to the React frontend — anything under a frontend/ directory, or any
    React component file. Keeps a bare package.json/tsconfig at a Python project's root out."""
    p = path.lower()
    return "frontend/" in p or p.startswith("frontend/") or p.endswith((".tsx", ".jsx"))


class ScaffoldLibrary:
    """Matches a blueprint file to a proven template for a known stack. Unknown stacks match
    nothing, so the Build engine falls back to full model generation — scaffolding is additive.
    Handles two stacks independently by file: a FastAPI+SQLite backend and a React+Vite frontend,
    so a full-stack goal scaffolds both."""

    def matches_backend(self, stack: str) -> bool:
        return "fastapi" in (stack or "").lower()

    def matches_frontend(self, stack: str) -> bool:
        s = (stack or "").lower()
        return any(k in s for k in ("react", "vite", "typescript", "tsx", "frontend"))

    def stack_matches(self, stack: str) -> bool:
        return self.matches_backend(stack) or self.matches_frontend(stack)

    def match(self, file: str, *, stack: str, root_pkg: str = "", main_mod: str = "main",
              app_name: str = "App") -> Scaffold | None:
        p = file.replace("\\", "/")
        base = p.rsplit("/", 1)[-1]

        # Frontend files → React templates (only if the stack calls for a frontend).
        if self.matches_frontend(stack) and _is_frontend_file(p):
            return self._match_frontend(p, base, app_name)
        if self.matches_backend(stack):
            return self._match_backend(p, base, root_pkg, main_mod, app_name)
        return None

    def _match_frontend(self, p: str, base: str, app_name: str) -> Scaffold | None:
        base = base.lower()                     # React files are conventionally CamelCase (App.tsx)

        def render(t: str) -> str:
            return t.replace("__APP_NAME__", app_name).replace("__APP_SLUG__", _slug(app_name))

        if base in _FRONTEND_COMPLETE:
            return Scaffold(render(_FRONTEND_COMPLETE[base]), True)
        if base in ("main.tsx", "main.jsx"):
            return Scaffold(render(_MAIN_TSX), True)
        if base in ("app.tsx", "app.jsx"):
            return Scaffold(render(_APP_TSX), False, "Build the interface the goal requires.")
        if base in ("api.ts", "api.tsx", "client.ts"):
            return Scaffold(render(_API_TS), False, "Add the API calls the UI needs.")
        return None

    def _match_backend(self, p: str, base: str, root_pkg: str, main_mod: str,
                       app_name: str) -> Scaffold | None:
        pkg = (root_pkg + ".") if root_pkg else ""

        def render(t: str) -> str:
            return (t.replace("__PKG__", pkg)
                     .replace("__APP_NAME__", app_name)
                     .replace("__MAINMOD__", main_mod))

        # Complete boilerplate — laid down as-is, the model is skipped.
        complete = {
            "config.py": _CONFIG,
            "db.py": _DB, "database.py": _DB,
            "errors.py": _ERRORS, "exceptions.py": _ERRORS,
            "sessions.py": _SESSIONS, "session.py": _SESSIONS,
            "requirements.txt": _REQUIREMENTS,
            "Dockerfile": _DOCKERFILE,
        }
        if base in complete:
            return Scaffold(render(complete[base]), True)
        if base.endswith(".yml") and (".github/workflows/" in p or "ci" in base):
            return Scaffold(render(_CI), True)

        # Skeletons — a working starting point the model completes with domain logic.
        if base == "models.py":
            return Scaffold(render(_MODELS), False, "Add the domain models the goal needs.")
        if base in ("main.py", "app.py"):
            return Scaffold(render(_MAIN), False, "Include the routers you build.")
        if base == "auth.py":
            return Scaffold(render(_AUTH), False,
                            "Complete register_user/authenticate_user against the User model.")
        if base in ("routes.py", "router.py") or "/routers/" in p or "/routes/" in p:
            return Scaffold(render(_ROUTES), False, "Add the endpoints the goal requires.")
        if base.startswith("test_") and base.endswith(".py"):
            return Scaffold(render(_TESTS), False, "Add real test assertions for the goal.")
        return None
