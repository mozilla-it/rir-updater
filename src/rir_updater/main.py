import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from rir_updater.arin.client import ArinClient
from rir_updater.config import load_config
from rir_updater.credentials import (
    get_arin_api_key,
    get_radb_mntner_password,
    get_radb_portal_auth,
    get_ripe_db_auth,
    get_ripe_rpki_key,
)
from rir_updater.exceptions import ApiError, CredentialError, RirUpdaterError
from rir_updater.radb.client import RadbClient
from rir_updater.ripe.client import RipeClient


def main():
    parser = argparse.ArgumentParser(description="Update RIR route objects and ROAs")
    parser.add_argument("config", type=Path, help="Path to config YAML file")
    parser.add_argument(
        "--commit", action="store_true", help="Apply changes (default is dry-run)"
    )
    parser.add_argument(
        "--production", action="store_true", help="Use production API (default: test)"
    )
    parser.add_argument(
        "--setup-test",
        action="store_true",
        help="Replicate required objects from production into the test database",
    )
    parser.add_argument(
        "--registry",
        action="append",
        choices=["ripe", "arin", "radb"],
        metavar="REGISTRY",
        dest="registries",
        help="Registry to update; may be repeated. Default: all configured.",
    )
    args = parser.parse_args()

    try:
        _run(args, parser)
    except FileNotFoundError:
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as e:
        print(f"error: invalid config:\n{e}", file=sys.stderr)
        sys.exit(1)
    except CredentialError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except RirUpdaterError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def _run(args, parser):
    config = load_config(args.config)

    selected = set(args.registries) if args.registries else None

    def should_run(name: str) -> bool:
        return selected is None or name in selected

    if args.setup_test:
        if args.production:
            parser.error("--setup-test cannot be used with --production")
        if selected and "ripe" not in selected:
            parser.error("--setup-test requires ripe to be selected")

    if config.ripe and should_run("ripe"):
        creds = config.ripe.credentials
        use_test_env = not args.production
        # The test DB may have a separate account (RIPE test accounts are distinct
        # from production). Fall back to production credentials if not configured.
        if use_test_env and creds.test_db_username and creds.test_db_password:
            db_auth = get_ripe_db_auth(creds.test_db_username, creds.test_db_password)
        else:
            db_auth = get_ripe_db_auth(creds.db_username, creds.db_password)
        with RipeClient(
            db_auth=db_auth,
            rpki_key=get_ripe_rpki_key(creds.rpki_api_key),
            maintainer=config.ripe.maintainer,
            dry_run=not args.commit,
            use_test_env=use_test_env,
        ) as client:
            if args.setup_test:
                client.setup_test_env(config.ripe.routes, config.ripe.sso_emails)
                return

            for route in config.ripe.routes:
                result = client.sync_route(route)
                print(f"{result}: ripe route {route.prefix} {route.origin}")

            if config.ripe.roas:
                counts = client.sync_roas(config.ripe.roas)
                added, deleted = counts["added"], counts["deleted"]
                print(f"RIPE ROAs: {added} added, {deleted} deleted")

    if config.arin and should_run("arin"):
        creds = config.arin.credentials
        use_test_env = not args.production
        if use_test_env and creds.test_api_key:
            arin_api_key = get_arin_api_key(creds.test_api_key)
        else:
            arin_api_key = get_arin_api_key(creds.api_key)
        with ArinClient(
            org_handle=config.arin.org_handle,
            api_key=arin_api_key,
            dry_run=not args.commit,
            use_test_env=use_test_env,
        ) as client:
            for route in config.arin.routes:
                if route.delete:
                    result = client.delete_route(route)
                else:
                    result = client.sync_route(route)
                print(f"{result}: arin route {route.prefix} {route.origin}")

            if config.arin.roas:
                counts = client.sync_roas(config.arin.roas)
                added, deleted = counts["added"], counts["deleted"]
                print(f"ARIN ROAs: {added} added, {deleted} deleted")

    if config.radb and should_run("radb"):
        creds = config.radb.credentials
        portal_username, portal_password = get_radb_portal_auth(
            creds.portal_username, creds.portal_password
        )
        with RadbClient(
            maintainer=config.radb.maintainer,
            portal_username=portal_username,
            portal_password=portal_password,
            mntner_password=get_radb_mntner_password(creds.mntner_password),
            contact_email=config.radb.contact_email,
            dry_run=not args.commit,
        ) as client:
            for route in config.radb.routes:
                result = client.sync_route(route)
                print(f"{result}: radb route {route.prefix} {route.origin}")


if __name__ == "__main__":
    main()
