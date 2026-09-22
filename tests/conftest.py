"""Test env vars must be set before `app.*` is imported anywhere, since
app/config.py reads them at import time. conftest.py is always imported
first by pytest, so this runs before any test module touches the app."""

import os
import tempfile
from pathlib import Path

_TEST_DB = Path(tempfile.mkdtemp()) / "test.db"

os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "testpass"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["SESSION_HTTPS_ONLY"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
