"""
config/env.py
=============
Django/Python equivalent of the Node.js `config/env.js` dotenv validator.

Usage
-----
Call ``validate_env()`` at Django startup — already wired into
``marian_backend/settings.py`` at module load time.

Mirrors the Node.js pattern::

    const { validateEnv } = require('./config/env');
    validateEnv();

Python equivalent::

    from config.env import validate_env
    validate_env()

Environment Loading
-------------------
- Reads ``.env`` from the project root (same directory as ``manage.py``).
- Uses ``os.environ.setdefault`` so real OS / CI environment variables
  always take priority over the ``.env`` file values (identical behaviour
  to ``dotenv``'s default ``override=False`` mode).
- Strips surrounding single/double quotes from values, e.g.::

      ADMIN_EMAILS="a@b.org,c@d.org"  →  a@b.org,c@d.org

Required Variables
------------------
All keys listed in ``REQUIRED_ENV_VARS`` must be present and non-empty.
On failure the module prints every missing key and exits with code 1,
mirroring ``process.exit(1)`` in the Node.js version.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root — one directory above this file (config/)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Required environment variable names
# (Python equivalents of the Node.js requiredEnvVars array)
# ---------------------------------------------------------------------------
REQUIRED_ENV_VARS: list[str] = [
    # Django core
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    # Admin access control
    "ADMIN_EMAILS",
    # Google SSO
    "GOOGLE_CLIENT_ID",
    # Database
    "DATABASE_NAME",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_HOST",
]


def _load_dot_env() -> None:
    """
    Load ``.env`` from the project root into ``os.environ``.

    Equivalent to::

        require('dotenv').config({ path: path.resolve(__dirname, '../.env') })

    Rules:
      - Skips blank lines and comment lines (``# ...``).
      - Splits on the first ``=`` only, so values may contain ``=``.
      - Strips surrounding single or double quotes.
      - Uses ``setdefault`` — real OS / CI env vars always win.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return  # No .env file — rely on real environment variables (CI/CD, Docker, etc.)

    with open(env_path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes  →  "value" becomes value
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            os.environ.setdefault(key, val)


def validate_env() -> None:
    """
    Validate that all required environment variables are present and non-empty.

    Equivalent to the Node.js ``validateEnv()`` function::

        function validateEnv() {
          const missing = requiredEnvVars.filter(key => !process.env[key]);
          if (missing.length > 0) {
            console.error('FATAL CONFIGURATION ERROR: ...');
            missing.forEach(key => console.error(`  - ${key}`));
            process.exit(1);
          }
          console.log(`Environment variables successfully validated (${NODE_ENV} mode).`);
        }

    Raises ``SystemExit(1)`` on failure so the server never starts in a
    misconfigured state — identical contract to ``process.exit(1)``.
    """
    _load_dot_env()

    missing = [key for key in REQUIRED_ENV_VARS if not os.environ.get(key, "").strip()]

    if missing:
        print(
            "\nFATAL CONFIGURATION ERROR: Missing required environment variables in .env file:",
            file=sys.stderr,
        )
        for key in missing:
            print(f"  - {key}", file=sys.stderr)
        print(
            "\nPlease refer to '.env.example' to configure your environment variables properly.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    django_env = "debug" if os.environ.get("DJANGO_DEBUG", "False").lower() == "true" else "production"
    print(f"[config/env.py] Environment variables successfully validated ({django_env} mode).")
