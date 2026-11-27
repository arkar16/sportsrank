"""Dynamic server-rendered SportsRank web site backed by read-only SQLite."""

from .app import create_app

__all__ = ["create_app"]
