"""Request/response schemas for the HTTP API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Ask the backend to inspect a URL and return available formats."""

    url: str = Field(..., min_length=3, max_length=2048)


class JobRequest(BaseModel):
    """Queue a download.

    ``plan_id`` refers to a plan produced by ``/api/analyze``.  When it is
    omitted the backend probes the URL itself so that a raw URL always works.
    """

    url: str = Field(..., min_length=3, max_length=2048)
    plan_id: Optional[str] = None
    quality_id: str = "best"
    mode: str = Field(default="video", pattern="^(video|audio)$")
    subtitles: bool = False


class BatchJobRequest(BaseModel):
    """Queue several downloads in one call."""

    items: List[JobRequest] = Field(..., min_length=1, max_length=100)
