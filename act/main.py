import argparse
import logging
import os


DEFAULT_SYSTEMS = ("vosslnx", "vosslnxft", "argon", "local")


def _configure_logging() -> None:

    log_file = os.getenv("LOG_FILE")
    handlers = []

    if log_file:

        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    else:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        handlers=handlers,
    )


def _daysago_type(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("daysago must be an integer") from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError("daysago must be non-negative")
    return parsed


def _token_type(value: str) -> str:
    # Allow empty for optional --targeting-token; --token stays required=True.
    return value


def _required_token_type(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("token must be a non-empty string")
    return value


def _available_systems() -> tuple[str, ...]:
    try:
        from act.utils.pipe import Pipe

        available = getattr(Pipe, "available_systems", None)
        if callable(available):
            return tuple(available())
    except Exception:
        pass

    return DEFAULT_SYSTEMS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m act.main",
        description=(
            "Run BOOST ingest pipeline using explicit typed arguments. "
            "Use --rebuild-manifest-only or --reconcile-manifest-only for "
            "manifest-only maintenance modes."
        ),
    )
    parser.add_argument(
        "--token",
        type=_required_token_type,
        required=True,
        help=(
            "Observational REDCap API token (BOOST_TOKEN) for reports 43327/41693"
        ),
    )
    parser.add_argument(
        "--targeting-token",
        type=_token_type,
        default="",
        dest="targeting_token",
        help=(
            "Intervention/Targeting REDCap API token for report 53846 "
            "(defaults to TARGETING_TOKEN or DEBRIEF_FITNESS_TOKEN env). "
            "Pass empty string to skip INT report."
        ),
    )
    parser.add_argument(
        "--daysago",
        type=_daysago_type,
        required=True,
        help="Lookback window in days (required, integer >= 0)",
    )
    parser.add_argument(
        "--system",
        choices=_available_systems(),
        required=True,
        help="Target system path profile",
    )
    manifest_mode_group = parser.add_mutually_exclusive_group()
    manifest_mode_group.add_argument(
        "--rebuild-manifest-only",
        action="store_true",
        help="Rebuild manifest-only mode (skips ingest copy, GGIR, and plotting)",
    )
    manifest_mode_group.add_argument(
        "--reconcile-manifest-only",
        action="store_true",
        help="Reconcile manifest-only mode (verifies or repairs canonical CSVs and skips GGIR/plotting)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from act.utils.group import Group
    from act.utils.pipe import Pipe

    _configure_logging()
    args = build_parser().parse_args(argv)

    targeting_token = (
        (args.targeting_token or "").strip()
        or os.environ.get("TARGETING_TOKEN", "").strip()
        or os.environ.get("DEBRIEF_FITNESS_TOKEN", "").strip()
    )
    if not targeting_token:
        logging.warning(
            "No Targeting token (--targeting-token / TARGETING_TOKEN); "
            "INT report 53846 will be skipped"
        )

    p = Pipe(
        token=args.token,
        daysago=args.daysago,
        system=args.system,
        rebuild_manifest_only=args.rebuild_manifest_only,
        reconcile_manifest_only=args.reconcile_manifest_only,
        targeting_token=targeting_token or None,
    )

    try:
        result = p.run_pipe()
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    if args.reconcile_manifest_only:
        report = result or {}
        logging.info(
            "reconcile_summary total=%s repaired=%s mismatched=%s missing_source=%s missing_dest=%s ambiguous_dest=%s",
            report.get("total_records", 0),
            report.get("repaired", 0),
            report.get("mismatched", 0),
            report.get("missing_source", 0),
            report.get("missing_dest", 0),
            report.get("ambiguous_dest", 0),
        )
        return 0 if not report.get("errors") else 1

    if not args.rebuild_manifest_only:
        Group(args.system).plot_person()
        Group(args.system).plot_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
