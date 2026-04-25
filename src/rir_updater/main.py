import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from rir_updater.config import load_config
from rir_updater.credentials import get_ripe_db_auth, get_ripe_rpki_key
from rir_updater.exceptions import ApiError, CredentialError, RirUpdaterError
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

    if config.ripe:
        with RipeClient(
            db_auth=get_ripe_db_auth(),
            rpki_key=get_ripe_rpki_key(),
            maintainer=config.ripe.maintainer,
            dry_run=not args.commit,
            use_test_env=not args.production,
        ) as client:
            if args.setup_test:
                if args.production:
                    parser.error("--setup-test cannot be used with --production")
                client.setup_test_env(config.ripe.routes, config.ripe.sso_emails)

            for route in config.ripe.routes:
                result = client.sync_route(route)
                print(f"{result}: route {route.prefix} {route.origin}")

            if config.ripe.roas:
                counts = client.sync_roas(config.ripe.roas)
                print(f"ROAs: {counts['added']} added, {counts['deleted']} deleted")


if __name__ == "__main__":
    main()
