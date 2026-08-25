"""Base Migration Interface for PayPilot Versioned Database Migrations (Phase 23).

Defines the abstract BaseMigration contract for forward (up) and rollback (down)
database schema evolutions with cryptographic checksum verification.
"""

from abc import ABC, abstractmethod
import hashlib
from typing import Any, Dict, Optional
from sqlalchemy.engine import Engine


class BaseMigration(ABC):
    """Abstract contract for an ordered database schema migration step."""

    version: str
    description: str

    @abstractmethod
    def up(self, engine: Engine) -> None:
        """Executes forward migration creating or modifying tables and indices."""
        raise NotImplementedError

    @abstractmethod
    def down(self, engine: Engine) -> None:
        """Reverts the changes made by up() cleanly."""
        raise NotImplementedError

    @classmethod
    def compute_checksum(cls) -> str:
        """Computes a deterministic SHA-256 fingerprint of the migration definition."""
        content = f"{cls.version}:{cls.description}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Serializes migration metadata."""
        return {
            "version": self.version,
            "description": self.description,
            "checksum": self.compute_checksum(),
        }
