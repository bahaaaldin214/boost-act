import argparse
import logging
import os


DEFAULT_SYSTEMS = ("vosslnx", "vosslnxft", "argon", "local", "extend")


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
        type=_token_type,
        default="",
        help="RedCap API token (required for ingest modes; optional with --ggir-only)",
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
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Optional GGIR output directory override",
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
    parser.add_argument(
        "--ggir-only",
        action="store_true",
        help="Run GGIR only (skip REDCap ingest, manifest, and group plots)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from act.utils.group import Group
    from act.utils.pipe import Pipe

    _configure_logging()
    args = build_parser().parse_args(argv)

    if not args.ggir_only and not args.token.strip():
        logging.error("--token is required unless --ggir-only is set")
        return 2

    p = Pipe(
        token=args.token,
        daysago=args.daysago,
        system=args.system,
        output_dir=args.output_dir,
        rebuild_manifest_only=args.rebuild_manifest_only,
        reconcile_manifest_only=args.reconcile_manifest_only,
        ggir_only=args.ggir_only,
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

    if not args.rebuild_manifest_only and not args.ggir_only:
        Group(args.system).plot_person()
        Group(args.system).plot_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
