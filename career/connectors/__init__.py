"""Connector registry."""

from __future__ import annotations

from .base import Connector, StagedItem, read_provenance, source_type_of, staging_dir, write_items
from .claude_code import ClaudeCodeConnector
from .codex import CodexConnector
from .notion_export import NotionExportConnector
from .x_archive import XArchiveConnector

REGISTRY: dict[str, Connector] = {c.name: c for c in (
    ClaudeCodeConnector(),
    CodexConnector(),
    NotionExportConnector(),
    XArchiveConnector(),
)}

__all__ = ["Connector", "StagedItem", "REGISTRY", "staging_dir", "source_type_of",
           "write_items", "read_provenance"]
