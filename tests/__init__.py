"""Test package defaults for imports that construct API clients at module load."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-api-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
# Tests never send traces, even if .env holds a real ZOROVAL_INGEST_KEY.
os.environ.setdefault("ZOROVAL_ENABLED", "0")
