# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Persist Datamesh connections as a native Datamesh Workspace.

A *connection* is a saved Datamesh query (a view of a datasource). The whole set
of connections is stored on disk in the published Datamesh workspace schema,
https://schemas.oceanum.io/datamesh/workspace.json, which defines the workspace
entity as an ordered **array** of ``WorkspaceItem`` objects::

    [
      { ...OceanQL query fields..., "id": <str>, "label": <str|null> },
      ...
    ]

A ``WorkspaceItem`` is an OceanQL query (``oceanum.datamesh.Query``) supplemented
with a required stable ``id`` and an optional display ``label``. Envelope
metadata (name, description, creator, modified) is *not* part of this entity —
the schema places it on the outer spec-store record — so the file we write is a
bare array. On read we also accept an enveloped ``{"spec": [...]}`` form for
resilience.

Kept free of QGIS imports so it is unit-testable standalone; ``oceanum`` is
imported lazily to match the rest of the plugin.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_URL = "https://schemas.oceanum.io/datamesh/workspace.json"


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Connection:
    """One workspace item: a stable id, an optional label and its OceanQL query.

    ``query`` is an ``oceanum.datamesh.Query``; ``label`` is the human-readable
    connection name shown in the Browser (distinct from the query's own
    ``description``).
    """

    id: str
    query: object
    label: str | None = None

    @property
    def datasource(self) -> str:
        return getattr(self.query, "datasource", "") or ""


def connection_label(conn: Connection) -> str:
    """Human-readable name for a connection: its label, else the datasource."""
    return (getattr(conn, "label", None) or conn.datasource or "").strip()


class ConnectionStore:
    """Load and save Datamesh connections as a published-schema workspace file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- raw items --------------------------------------------------------- #
    def _read_items(self) -> list[dict]:
        """Return the raw WorkspaceItem dicts (empty on missing/blank/corrupt).

        Malformed JSON degrades to an empty list (logged) rather than raising,
        so one bad file cannot brick the Browser tree or block saving.
        """
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            data = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read connections from %s: %s", self.path, exc)
            return []
        # The workspace entity is a bare array; tolerate an enveloped record.
        if isinstance(data, dict):
            data = data.get("spec") or data.get("data") or []
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _item_id(item: dict) -> str | None:
        value = item.get("id")
        return value if isinstance(value, str) else None

    def _write_items(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    @staticmethod
    def _item(query: Any, connection_id: str, label: str | None) -> dict:
        item = query.model_dump(mode="json", warnings=False)
        item["id"] = connection_id
        item["label"] = label
        return item

    # -- connection operations -------------------------------------------- #
    def list(self) -> list[Connection]:
        """Return the stored connections, skipping any that fail to parse."""
        from oceanum.datamesh import Query

        allowed = set(Query.model_fields)
        connections = []
        for raw in self._read_items():
            raw = dict(raw)
            label = raw.pop("label", None)
            cid = raw.get("id") or _new_id()
            raw["id"] = cid
            try:
                query = Query(**{k: v for k, v in raw.items() if k in allowed})
            except Exception as exc:  # noqa: BLE001 - one bad item must not poison the rest
                logger.warning("Skipping unreadable connection %s: %s", cid, exc)
                continue
            connections.append(Connection(id=cid, query=query, label=label))
        return connections

    def get(self, connection_id: str) -> Connection | None:
        for conn in self.list():
            if conn.id == connection_id:
                return conn
        return None

    def add(self, query: Any, label: str | None = None) -> str:
        """Append a connection, assigning an id if it has none. Returns the id.

        Operates on the raw item list so fields written by other Datamesh tools
        (or a newer schema) on untouched connections are preserved verbatim.
        """
        cid = getattr(query, "id", None) or _new_id()
        query.id = cid
        items = self._read_items()
        items.append(self._item(query, cid, label))
        self._write_items(items)
        return cid

    def update(self, connection_id: str, query: Any, label: str | None = None) -> None:
        """Replace the connection ``connection_id`` with *query* / *label*."""
        query.id = connection_id
        replacement = self._item(query, connection_id, label)
        items = [
            replacement if self._item_id(it) == connection_id else it for it in self._read_items()
        ]
        self._write_items(items)

    def remove(self, connection_id: str) -> None:
        items = [it for it in self._read_items() if self._item_id(it) != connection_id]
        self._write_items(items)
