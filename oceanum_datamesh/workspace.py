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
import uuid
from dataclasses import dataclass
from pathlib import Path

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

    def __init__(self, path):
        self.path = Path(path)

    # -- raw items --------------------------------------------------------- #
    def _read_items(self) -> list[dict]:
        """Return the raw WorkspaceItem dicts (empty if no/blank file)."""
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        # The workspace entity is a bare array; tolerate an enveloped record.
        if isinstance(data, dict):
            data = data.get("spec") or data.get("data") or []
        return list(data)

    def _write(self, connections: list[Connection]) -> None:
        items = []
        for conn in connections:
            item = conn.query.model_dump(mode="json", warnings=False)
            item["id"] = conn.id
            item["label"] = conn.label
            items.append(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    # -- connection operations -------------------------------------------- #
    def list(self) -> list[Connection]:
        """Return the stored connections."""
        from oceanum.datamesh import Query

        allowed = set(Query.model_fields)
        connections = []
        for raw in self._read_items():
            raw = dict(raw)
            label = raw.pop("label", None)
            cid = raw.get("id") or _new_id()
            raw["id"] = cid
            query = Query(**{k: v for k, v in raw.items() if k in allowed})
            connections.append(Connection(id=cid, query=query, label=label))
        return connections

    def get(self, connection_id: str) -> Connection | None:
        for conn in self.list():
            if conn.id == connection_id:
                return conn
        return None

    def add(self, query, label: str | None = None) -> str:
        """Append a connection, assigning an id if it has none. Returns the id."""
        cid = getattr(query, "id", None) or _new_id()
        query.id = cid
        connections = self.list()
        connections.append(Connection(id=cid, query=query, label=label))
        self._write(connections)
        return cid

    def update(self, connection_id: str, query, label: str | None = None) -> None:
        """Replace the connection ``connection_id`` with *query* / *label*."""
        query.id = connection_id
        replacement = Connection(id=connection_id, query=query, label=label)
        connections = [replacement if c.id == connection_id else c for c in self.list()]
        self._write(connections)

    def remove(self, connection_id: str) -> None:
        connections = [c for c in self.list() if c.id != connection_id]
        self._write(connections)
