"""Pytest configuration and session-wide SQLite isolation fixture."""

from pathlib import Path
import pytest

from app.core.config import settings
from app.core.database import db_manager
from app.payment.repository import in_memory_payment_repo
from app.policy.repository import in_memory_policy_repo


@pytest.fixture(scope="session", autouse=True)
def setup_test_sqlite_database(tmp_path_factory):
    """Ensure all automated tests run against an isolated temporary SQLite file."""
    temp_dir = tmp_path_factory.mktemp("sqlite_test_data")
    test_db_path = str(temp_dir / "test_checkout_helper.db")

    # Point settings and db_manager to the temporary test database
    settings.SQLITE_DB_PATH = test_db_path
    db_manager.db_path_str = test_db_path
    db_manager._ensure_parent_directory()
    db_manager.init_db()

    # Bind repositories to the isolated database manager
    in_memory_policy_repo.db = db_manager
    in_memory_payment_repo.db = db_manager

    yield test_db_path
