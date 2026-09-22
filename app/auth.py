import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from .config import ADMIN_PASSWORD, ADMIN_USERNAME

# Simple in-memory login rate limit: per-process only, resets on restart.
# Adequate here because the app runs as a single uvicorn worker/process.
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 15 * 60


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(request: Request) -> bool:
    ip = _client_ip(request)
    now = time.monotonic()
    attempts = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = attempts
    return len(attempts) < _MAX_ATTEMPTS


def register_login_failure(request: Request) -> None:
    _LOGIN_ATTEMPTS[_client_ip(request)].append(time.monotonic())


def clear_login_attempts(request: Request) -> None:
    _LOGIN_ATTEMPTS.pop(_client_ip(request), None)


def verify_credentials(username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(username, ADMIN_USERNAME)
    pass_ok = secrets.compare_digest(password, ADMIN_PASSWORD)
    return user_ok and pass_ok


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin"))


def require_admin(request: Request) -> None:
    if not is_logged_in(request):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf")
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=400, detail="Sessao invalida, tenta novamente.")
