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
from rir_updater.summary import Summary


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
        "--setup-ote",
        action="store_true",
        help="Replicate ARIN production routes and ROAs into the OTE environment",
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

    summary = None
    try:
        summary = _run(args, parser)
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

    # Per-object failures don't raise; they are collected and reported in the
    # summary. Exit non-zero so callers/CI still see that something failed.
    if summary is not None and summary.has_errors():
        sys.exit(1)


def _try(summary, registry, label, fn):
    """Run fn(); on ApiError record it against (registry, label) and return None.

    Isolates per-object failures so one bad object doesn't abort the whole run —
    the remaining objects still sync and every failure is reported in the summary.
    """
    try:
        return fn()
    except ApiError as e:
        summary.record_error(registry, label, str(e))
        return None


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

    if args.setup_ote:
        if args.production:
            parser.error("--setup-ote cannot be used with --production")
        if not config.arin:
            parser.error("--setup-ote requires an arin section in config")
        creds = config.arin.credentials
        if not creds.test_api_key:
            parser.error("--setup-ote requires arin.credentials.test_api_key in config")
        _setup_arin_ote(config.arin, creds, args.commit)
        return

    summary = Summary(dry_run=not args.commit)
    mirrored_prefixes: set[str] = set()

    # Build RadbClient upfront — used for both explicit RADb routes and
    # automatic mirroring of every RIPE/ARIN route change.
    radb_client = None
    if config.radb:
        radb_creds = config.radb.credentials
        radb_portal_u, radb_portal_p = get_radb_portal_auth(
            radb_creds.portal_username, radb_creds.portal_password
        )
        radb_client = RadbClient(
            maintainer=config.radb.maintainer,
            portal_username=radb_portal_u,
            portal_password=radb_portal_p,
            mntner_password=get_radb_mntner_password(radb_creds.mntner_password),
            contact_email=config.radb.contact_email,
            dry_run=not args.commit,
        )

    try:
        if config.ripe and should_run("ripe"):
            label = "RIPE (production)" if args.production else "RIPE (test)"
            creds = config.ripe.credentials
            use_test_env = not args.production
            # The test DB may have a separate account (RIPE test accounts are
            # distinct from production). Fall back to production creds if not set.
            if use_test_env and creds.test_db_username and creds.test_db_password:
                db_auth = get_ripe_db_auth(
                    creds.test_db_username, creds.test_db_password
                )
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

                summary.start_registry(label)
                for route in config.ripe.routes:
                    result = _try(
                        summary,
                        label,
                        route.prefix,
                        lambda r=route: (
                            client.delete_route(r) if r.delete else client.sync_route(r)
                        ),
                    )
                    if result is not None:
                        summary.record_route(label, result, route.prefix, route.origin)
                    if radb_client:
                        radb_result = _try(
                            summary,
                            "RADb",
                            route.prefix,
                            lambda r=route: (
                                radb_client.delete_route(r)
                                if r.delete
                                else radb_client.sync_route(r)
                            ),
                        )
                        if radb_result is not None:
                            summary.record_route(
                                "RADb", radb_result, route.prefix, route.origin
                            )
                        mirrored_prefixes.add(route.prefix)

                if config.ripe.roas:
                    counts = _try(
                        summary,
                        label,
                        "ROAs",
                        lambda: client.sync_roas(config.ripe.roas),
                    )
                    if counts is not None:
                        summary.record_roas(label, counts["added"], counts["deleted"])

        if config.arin and should_run("arin"):
            label = "ARIN (production)" if args.production else "ARIN (OTE)"
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
                summary.start_registry(label)
                for route in config.arin.routes:
                    result = _try(
                        summary,
                        label,
                        route.prefix,
                        lambda r=route: (
                            client.delete_route(r) if r.delete else client.sync_route(r)
                        ),
                    )
                    if result is not None:
                        summary.record_route(label, result, route.prefix, route.origin)
                    if radb_client:
                        radb_result = _try(
                            summary,
                            "RADb",
                            route.prefix,
                            lambda r=route: (
                                radb_client.delete_route(r)
                                if r.delete
                                else radb_client.sync_route(r)
                            ),
                        )
                        if radb_result is not None:
                            summary.record_route(
                                "RADb", radb_result, route.prefix, route.origin
                            )
                        mirrored_prefixes.add(route.prefix)

                if config.arin.roas:
                    counts = _try(
                        summary,
                        label,
                        "ROAs",
                        lambda: client.sync_roas(config.arin.roas),
                    )
                    if counts is not None:
                        summary.record_roas(label, counts["added"], counts["deleted"])

        if radb_client and should_run("radb"):
            summary.start_registry("RADb")
            for route in config.radb.routes:
                if route.prefix in mirrored_prefixes:
                    continue  # already synced via mirroring
                result = _try(
                    summary,
                    "RADb",
                    route.prefix,
                    lambda r=route: (
                        radb_client.delete_route(r)
                        if r.delete
                        else radb_client.sync_route(r)
                    ),
                )
                if result is not None:
                    summary.record_route("RADb", result, route.prefix, route.origin)

    finally:
        if radb_client:
            radb_client.close()

    summary.print_jira()
    return summary


def _setup_arin_ote(arin_config, creds, commit: bool) -> None:
    """Replicate routes and ROAs from ARIN production into OTE."""
    prod_key = get_arin_api_key(creds.api_key)
    ote_key = get_arin_api_key(creds.test_api_key)

    with ArinClient(
        org_handle=arin_config.org_handle,
        api_key=prod_key,
        dry_run=False,
        use_test_env=False,
    ) as prod:
        routes = prod.list_routes()
        roas = prod.list_roas()

    print(f"Found {len(routes)} routes and {len(roas)} ROAs in ARIN production")

    with ArinClient(
        org_handle=arin_config.org_handle,
        api_key=ote_key,
        dry_run=not commit,
        use_test_env=True,
    ) as ote:
        for route in routes:
            result = ote.sync_route(route)
            print(f"{result}: arin route {route.prefix} {route.origin}")
        if roas:
            counts = ote.sync_roas(roas)
            added, deleted = counts["added"], counts["deleted"]
            print(f"ARIN OTE ROAs: {added} added, {deleted} deleted")


if __name__ == "__main__":
    main()
