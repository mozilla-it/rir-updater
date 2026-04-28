import ipaddress
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

_ASN_RE = re.compile(r"^AS\d+$", re.IGNORECASE)


def _validate_prefix(value: str) -> str:
    try:
        ipaddress.ip_network(value, strict=True)
    except ValueError:
        raise ValueError(
            f"invalid prefix {value!r} — must be a network address in CIDR notation "
            "(e.g. '192.0.2.0/24' or '2001:db8::/32'), host bits must be zero"
        )
    return value


def _validate_asn(value: str) -> str:
    if not _ASN_RE.match(value):
        raise ValueError(
            f"invalid ASN {value!r} — must be in 'AS<number>' format (e.g. 'AS64496')"
        )
    return value.upper()


class RouteObject(BaseModel):
    """A route or route6 object to be created/updated in an IRR database."""

    prefix: str
    origin: str
    description: str = ""

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, v: str) -> str:
        return _validate_prefix(v)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, v: str) -> str:
        return _validate_asn(v)


class ROA(BaseModel):
    """An RPKI Route Origin Authorization to be managed via the RIPE RPKI API."""

    prefix: str
    origin: str
    # If omitted, max_length defaults to the prefix length (exact-match only).
    max_length: int | None = None

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, v: str) -> str:
        return _validate_prefix(v)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, v: str) -> str:
        return _validate_asn(v)

    @field_validator("max_length")
    @classmethod
    def validate_max_length(cls, v: int | None, info) -> int | None:
        if v is None:
            return v
        prefix = info.data.get("prefix", "")
        if prefix:
            prefix_len = int(prefix.split("/")[1])
            if v < prefix_len:
                raise ValueError(
                    f"max_length {v} is less than prefix length {prefix_len}"
                )
        return v


class RipeCredentials(BaseModel):
    """1Password references for RIPE credentials. Resolved at runtime via `op read`."""

    db_username: str
    db_password: str
    rpki_api_key: str
    # When present, these override db_username/db_password in test mode.
    test_db_username: str | None = None
    test_db_password: str | None = None


class RipeConfig(BaseModel):
    """Configuration for the RIPE NCC registry (DB objects and RPKI ROAs)."""

    maintainer: str
    credentials: RipeCredentials
    # SSO emails are used to set auth entries on the test mntner (--setup-test only).
    sso_emails: list[str] = []
    routes: list[RouteObject] = []
    roas: list[ROA] = []


class RadbCredentials(BaseModel):
    """1Password references for RADb credentials. Resolved at runtime via `op read`."""

    # Portal credentials are used for HTTP Basic auth on all REST API requests.
    portal_username: str
    portal_password: str
    # Mntner password is passed as ?password= for object-level RPSL authorization.
    mntner_password: str


class RadbConfig(BaseModel):
    """Configuration for the RADb registry (always runs against production)."""

    maintainer: str
    contact_email: str
    credentials: RadbCredentials
    routes: list[RouteObject] = []


class Config(BaseModel):
    ripe: RipeConfig | None = None
    radb: RadbConfig | None = None


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    return Config.model_validate(raw)
