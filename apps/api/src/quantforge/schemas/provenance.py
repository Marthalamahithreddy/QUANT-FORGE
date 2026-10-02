"""Data source (provenance) schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantforge.models.base import SHORT_NAME_LENGTH, URL_LENGTH
from quantforge.models.enums import ProviderType
from quantforge.schemas.common import OrmReadSchema, blank_to_none

__all__ = ["DataSourceCreate", "DataSourceRead"]


class DataSourceCreate(BaseModel):
    """Payload for registering a data provider.

    ``base_url`` is optional because a provider need not be reachable over HTTP
    (a delivered file, a manual transcription) and because recording an
    operational endpoint here is often a mistake. When present it must be
    http(s), matching the database ``CHECK``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=SHORT_NAME_LENGTH)
    provider_type: ProviderType
    base_url: str | None = Field(default=None, max_length=URL_LENGTH)
    is_active: bool = True

    @field_validator("base_url")
    @classmethod
    def _check_scheme(cls, value: str | None) -> str | None:
        """Require an http(s) scheme when a URL is supplied."""
        normalised = blank_to_none(value)
        if normalised is not None and not normalised.startswith(("http://", "https://")):
            message = f"base_url must be an http(s) URL, got {value!r}."
            raise ValueError(message)
        return normalised


class DataSourceRead(OrmReadSchema):
    """Read model for a registered provider."""

    id: UUID
    name: str
    provider_type: ProviderType
    base_url: str | None
    is_active: bool
    created_at: datetime
