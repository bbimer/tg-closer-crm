import os
import sys
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.repository import Repository


@pytest_asyncio.fixture
async def temp_repo():
    """Provides a fresh temporary SQLite repository for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db_path = Path(tf.name)

    repo = Repository(db_path=temp_db_path)
    await repo.init_db()

    yield repo

    # Cleanup temp file
    try:
        temp_db_path.unlink(missing_ok=True)
    except Exception:
        pass
