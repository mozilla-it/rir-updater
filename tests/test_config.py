import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from rir_updater.config import ROA, Config, RouteObject, load_config


class TestRouteObject:
    def test_valid_ipv4(self):
        r = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
        assert r.prefix == "192.0.2.0/24"
        assert r.origin == "AS64496"

    def test_valid_ipv6(self):
        r = RouteObject(prefix="2001:db8::/32", origin="AS64496")
        assert r.prefix == "2001:db8::/32"

    def test_asn_normalised_to_uppercase(self):
        r = RouteObject(prefix="192.0.2.0/24", origin="as64496")
        assert r.origin == "AS64496"

    def test_invalid_prefix_host_bits_set(self):
        with pytest.raises(ValidationError, match="host bits"):
            RouteObject(prefix="192.0.2.1/24", origin="AS64496")

    def test_invalid_prefix_not_cidr(self):
        with pytest.raises(ValidationError):
            RouteObject(prefix="notaprefix", origin="AS64496")

    def test_invalid_asn_no_prefix(self):
        with pytest.raises(ValidationError, match="AS<number>"):
            RouteObject(prefix="192.0.2.0/24", origin="64496")

    def test_invalid_asn_with_space(self):
        with pytest.raises(ValidationError, match="AS<number>"):
            RouteObject(prefix="192.0.2.0/24", origin="AS 64496")

    def test_description_defaults_to_empty(self):
        r = RouteObject(prefix="192.0.2.0/24", origin="AS64496")
        assert r.description == ""


class TestROA:
    def test_valid_roa(self):
        r = ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=24)
        assert r.max_length == 24

    def test_max_length_defaults_to_none(self):
        r = ROA(prefix="192.0.2.0/24", origin="AS64496")
        assert r.max_length is None

    def test_max_length_greater_than_prefix_len(self):
        r = ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=25)
        assert r.max_length == 25

    def test_max_length_less_than_prefix_len(self):
        with pytest.raises(ValidationError, match="less than prefix length"):
            ROA(prefix="192.0.2.0/24", origin="AS64496", max_length=23)


class TestLoadConfig:
    CREDS = textwrap.dedent("""\
        credentials:
          db_username: "op://vault/item/username"
          db_password: "op://vault/item/password"
          rpki_api_key: "op://vault/item/rpki-api-key"
    """)

    def test_load_valid_config(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
                ripe:
                  maintainer: MAINT-AS64496
                  credentials:
                    db_username: "op://vault/item/username"
                    db_password: "op://vault/item/password"
                    rpki_api_key: "op://vault/item/rpki-api-key"
                  routes:
                    - prefix: "192.0.2.0/24"
                      origin: AS64496
                      description: Test prefix
                  roas:
                    - prefix: "192.0.2.0/24"
                      origin: AS64496
                      max_length: 24
            """)
        )
        config = load_config(cfg)
        assert config.ripe is not None
        assert config.ripe.maintainer == "MAINT-AS64496"
        assert len(config.ripe.routes) == 1
        assert len(config.ripe.roas) == 1

    def test_empty_ripe_section(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
                ripe:
                  maintainer: MAINT-AS64496
                  credentials:
                    db_username: "op://vault/item/username"
                    db_password: "op://vault/item/password"
                    rpki_api_key: "op://vault/item/rpki-api-key"
            """)
        )
        config = load_config(cfg)
        assert config.ripe.routes == []
        assert config.ripe.roas == []

    def test_invalid_prefix_in_config(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
                ripe:
                  maintainer: MAINT-AS64496
                  credentials:
                    db_username: "op://vault/item/username"
                    db_password: "op://vault/item/password"
                    rpki_api_key: "op://vault/item/rpki-api-key"
                  routes:
                    - prefix: "192.0.2.1/24"
                      origin: AS64496
            """)
        )
        with pytest.raises(ValidationError):
            load_config(cfg)

    def test_no_ripe_section(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("{}\n")
        config = load_config(cfg)
        assert config == Config(ripe=None)
