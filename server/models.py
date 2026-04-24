from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class User:
    id: int
    identity: str
    created_at: str


@dataclass(slots=True)
class Project:
    id: int
    user_id: int
    target: str
    source_kind: str
    language: str
    updated_at: str


@dataclass(slots=True)
class ScanArtifact:
    id: int
    user_id: int
    project_id: int
    graph_json: str
    scan_context_json: str
    created_at: str


@dataclass(slots=True)
class JobRecord:
    id: str
    user_id: int
    job_type: str
    status: str
    created_at: str
    updated_at: str
