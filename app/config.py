import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATABASE_PATH = os.path.join(DATA_DIR, "chess.db")
# Overridable so tests (and any future deployment that wants a different
# location) don't have to share the dev/prod database file.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required (no insecure default is provided). "
            f"See .env.example."
        )
    return value


SECRET_KEY = _require_env("SECRET_KEY")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = _require_env("ADMIN_PASSWORD")

# Session cookies get the Secure flag by default (only sent over HTTPS). Set to
# "false" only for local/plain-HTTP testing before you put this behind TLS —
# otherwise the browser will silently refuse to send the admin session cookie
# back and login will appear broken.
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "true").lower() not in ("false", "0", "no")

# Both unset by default: the GoatCounter tracking snippet is only rendered
# once both are present, so local/dev runs and deployments that haven't set
# up analytics yet don't get a broken script tag pointed at nothing.
GOATCOUNTER_SCRIPT_URL = os.environ.get("GOATCOUNTER_SCRIPT_URL", "")
GOATCOUNTER_ENDPOINT = os.environ.get("GOATCOUNTER_ENDPOINT", "")
