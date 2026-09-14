import errno
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from act.utils.comparison_utils import ID_COMPARISONS


class Save:
    logger = logging.getLogger(__name__)

    # REDCap sometimes stores dual-enrollment as a single comma-joined boost_id
    # cell (e.g. "7178, 8066"). Historically these keys were silently dropped.
    # Expand them into the duplicates path instead.
    _COMBINED_BOOST_RE = re.compile(r"^(\d+)\s*,\s*(\d+)$")

    def __init__(
        self,
        intdir,
        obsdir,
        rdssdir,
        token,
        daysago=None,
        symlink=True,
        manifest_path="res/data.json",
        subject_ids=None,
        study_filter=None,
    ):
        if not rdssdir:
            raise ValueError(
                "RDSS directory is not configured for this system; cannot ingest files."
            )

        results = ID_COMPARISONS(
            rdss_dir=rdssdir, token=token, daysago=daysago
        ).compare_ids()
        self.matches = results["matches"]
        self.dupes = results["duplicates"]
        self._expand_combined_boost_keys()
        self.INT_DIR = intdir
        self.OBS_DIR = obsdir
        self.RDSS_DIR = rdssdir
        self.token = token
        self.daysago = daysago
        self.symlink = symlink
        self.manifest_path = manifest_path
        self.manifest = {}
        self.subject_ids = (
            {str(s).strip() for s in subject_ids if str(s).strip()}
            if subject_ids
            else None
        )
        study = (study_filter or "").strip().lower()
        if study and study not in {"obs", "int", "both"}:
            raise ValueError("study_filter must be one of: obs, int, both")
        self.study_filter = None if study in {"", "both"} else study

    def _expand_combined_boost_keys(self):
        """Turn '7178, 8066'-style match keys into dual-enrollment duplicate rows."""
        combined_keys = [
            key
            for key in list(self.matches.keys())
            if self._COMBINED_BOOST_RE.match(str(key))
        ]
        for key in combined_keys:
            match = self._COMBINED_BOOST_RE.match(str(key))
            obs_id, int_id = match.group(1), match.group(2)
            # Normalize which ID is OBS vs INT by band.
            left, right = int(obs_id), int(int_id)
            if left >= 8000 and right < 8000:
                obs_id, int_id = str(right), str(left)
            elif not (left < 8000 and right >= 8000):
                self.logger.warning(
                    "Skipping combined boost_id key %s; expected one OBS (<8000) "
                    "and one INT (>=8000) id.",
                    key,
                )
                self.matches.pop(key, None)
                continue

            records = self.matches.pop(key) or []
            if not records:
                continue
            lab_id = str(records[0].get("labID") or records[0].get("lab_id") or "")
            filenames = [r.get("filename") for r in records if r.get("filename")]
            dates = [r.get("date") for r in records]
            self.logger.info(
                "Expanding combined boost_id %s -> obs=%s int=%s lab=%s (%s files)",
                key,
                obs_id,
                int_id,
                lab_id,
                len(filenames),
            )
            for boost_id in (obs_id, int_id):
                self.dupes.append(
                    {
                        "lab_id": lab_id,
                        "boost_id": boost_id,
                        "filenames": list(filenames),
                        "dates": list(dates),
                    }
                )

    def _filter_matches(self, matches):
        filtered = matches
        if self.subject_ids is not None:
            filtered = {
                sid: records
                for sid, records in filtered.items()
                if str(sid) in self.subject_ids
            }
            self.logger.info(
                "Subject filter active (%s ids): %s remaining subjects",
                len(self.subject_ids),
                len(filtered),
            )
        if self.study_filter:
            next_filtered = {}
            for sid, records in filtered.items():
                kept = [
                    r
                    for r in records
                    if (r or {}).get("study", "").lower() == self.study_filter
                ]
                if kept:
                    next_filtered[sid] = kept
            filtered = next_filtered
            self.logger.info(
                "Study filter=%s: %s subjects remaining",
                self.study_filter,
                len(filtered),
            )
        return filtered

    def save(self):
        self.manifest = self._load_manifest(
            getattr(self, "manifest_path", "res/data.json")
        )

        # First, process the base matches.
        matches = self._determine_run(matches=self.matches)
        matches = self._determine_study(matches=matches)
        matches = self._determine_location(matches=matches)

        # If duplicates exist, process and merge them.
        if not len(self.dupes) == 0:
            matches = self._handle_and_merge_duplicates(self.dupes)

        matches = self._filter_matches(matches)

        for subject_id, records in matches.items():
            self._process_subject_transaction(subject_id, records)

        persisted_manifest = self._save_manifest(self.manifest_path)
        return self._prepare_for_json(persisted_manifest)

    def _normalize_manifest_payload(self, payload):
        if not isinstance(payload, dict):
            self.logger.warning(
                "Manifest payload is not a dict; using empty fallback payload."
            )
            return {}

        normalized = {}
        for subject_id, records in payload.items():
            subject_key = str(subject_id)
            if not isinstance(records, list):
                self.logger.warning(
                    "Manifest subject %s payload is not a list; coercing to empty list.",
                    subject_key,
                )
                normalized[subject_key] = []
                continue

            normalized_records = []
            for record in records:
                if isinstance(record, dict):
                    normalized_records.append(dict(record))
                else:
                    self.logger.warning(
                        "Manifest subject %s contains non-dict record; skipping record.",
                        subject_key,
                    )

            normalized[subject_key] = normalized_records

        return normalized

    def _load_manifest(self, path):
        manifest_path = path or getattr(self, "manifest_path", "res/data.json")

        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            self.logger.warning(
                "Manifest file not found at %s; using empty fallback payload.",
                manifest_path,
            )
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning(
                "Unable to load manifest from %s (%s); using empty fallback payload.",
                manifest_path,
                exc,
            )
            return {}

        return self._normalize_manifest_payload(payload)

    def _save_manifest(self, path):
        manifest_path = path or getattr(self, "manifest_path", "res/data.json")
        payload = self._normalize_manifest_payload(getattr(self, "manifest", {}))
        payload = self._prepare_for_json(payload)

        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

        return payload

    def _atomic_write_manifest(self, payload, path=None):
        manifest_path = path or getattr(self, "manifest_path", "res/data.json")
        normalized_payload = self._normalize_manifest_payload(payload)
        normalized_payload = self._prepare_for_json(normalized_payload)

        manifest_dir = os.path.dirname(manifest_path) or "."
        os.makedirs(manifest_dir, exist_ok=True)

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=".manifest-",
                suffix=".json",
                dir=manifest_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(normalized_payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temp_path, manifest_path)

            try:
                dir_fd = os.open(manifest_dir, os.O_DIRECTORY)
            except (AttributeError, OSError):
                dir_fd = None

            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)

            return normalized_payload
        except Exception:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

    def _normalize_record_date_value(self, date_value):
        if date_value is None:
            return None

        if hasattr(date_value, "to_pydatetime"):
            try:
                date_value = date_value.to_pydatetime()
            except Exception:
                pass

        if isinstance(date_value, datetime):
            return date_value.date().isoformat()

        if isinstance(date_value, date):
            return date_value.isoformat()

        if isinstance(date_value, str):
            normalized = date_value.strip()
            if not normalized:
                return normalized

            parse_candidates = (normalized, normalized.replace("Z", "+00:00"))
            for candidate in parse_candidates:
                try:
                    parsed = datetime.fromisoformat(candidate)
                except ValueError:
                    continue
                return parsed.date().isoformat()

            return normalized

        if hasattr(date_value, "isoformat"):
            return date_value.isoformat()

        return str(date_value)

    def _reindex_subject_records(self, existing_records, incoming_records):
        merged_records = []
        seen_keys = set()

        for record_list in (existing_records or [], incoming_records or []):
            if not isinstance(record_list, list):
                continue

            for record in record_list:
                if not isinstance(record, dict):
                    continue

                normalized_record = dict(record)
                normalized_record["date"] = self._normalize_record_date_value(
                    normalized_record.get("date")
                )

                dedupe_key = (
                    str(normalized_record.get("labID", "")),
                    normalized_record.get("date"),
                    str(normalized_record.get("filename", "")),
                )

                if dedupe_key in seen_keys:
                    continue

                seen_keys.add(dedupe_key)
                merged_records.append(normalized_record)

        return merged_records

    def _subject_sort_key(self, record):
        normalized_date = self._normalize_record_date_value(record.get("date")) or ""
        filename = str(record.get("filename", ""))
        lab_id = str(record.get("labID", ""))
        return (normalized_date, filename, lab_id)

    def _detect_same_date_conflict(self, records):
        seen_dates = set()
        for record in records:
            normalized_date = self._normalize_record_date_value(record.get("date"))
            if normalized_date is None:
                continue
            if normalized_date in seen_dates:
                return True
            seen_dates.add(normalized_date)
        return False

    def _record_identity_key(self, record):
        return (
            str(record.get("labID", "")),
            self._normalize_record_date_value(record.get("date")),
            str(record.get("filename", "")),
        )

    def _subject_study_root(self, study):
        if (study or "").lower() == "int":
            return self.INT_DIR
        return self.OBS_DIR

    def _fetch_redcap_subject_lab_rows(self):
        token = getattr(self, "token", None)
        if not token:
            raise ValueError("RedCap token is required to fetch subject mappings")

        rdss_dir = getattr(self, "RDSS_DIR", None) or "."
        daysago = getattr(self, "daysago", None)

        report_df, _ = ID_COMPARISONS(
            rdss_dir=rdss_dir,
            token=token,
            daysago=daysago,
        )._return_report()
        return report_df

    def resolve_subject_lab_mapping(self, subject_ids):
        requested_subjects = sorted(
            {
                str(subject_id)
                for subject_id in (subject_ids or [])
                if str(subject_id).strip()
            }
        )
        if not requested_subjects:
            return {}

        report_rows = self._fetch_redcap_subject_lab_rows()
        if hasattr(report_rows, "to_dict"):
            report_rows = report_rows.to_dict(orient="records")

        mapping, missing_subjects = self._build_subject_lab_mapping(
            requested_subjects,
            report_rows,
        )

        if missing_subjects:
            raise ValueError(
                "Missing RedCap subject->lab mappings for subject(s): "
                + ", ".join(missing_subjects)
            )

        return {subject_id: mapping[subject_id] for subject_id in requested_subjects}

    def _build_subject_lab_mapping(self, requested_subjects, report_rows):
        requested_subjects = [str(subject_id) for subject_id in (requested_subjects or [])]

        mapping = {}
        for row in report_rows or []:
            if not isinstance(row, dict):
                continue

            boost_id = row.get("boost_id")
            lab_id = row.get("lab_id")
            if boost_id is None or lab_id is None:
                continue

            mapping[str(boost_id)] = str(lab_id)

        missing_subjects = [
            subject_id for subject_id in requested_subjects if subject_id not in mapping
        ]
        return mapping, missing_subjects

    def _subject_order_key(self, subject_id):
        subject = str(subject_id)
        try:
            return (0, int(subject))
        except ValueError:
            return (1, subject)

    def _subject_exempt_from_manifest_metadata_strictness(self, subject_id):
        subject_text = str(subject_id).strip()
        if subject_text.startswith("9000"):
            return True

        try:
            subject_value = int(subject_text)
        except (TypeError, ValueError):
            return False

        return 9000 <= subject_value <= 9999

    def _resolve_rdss_session_metadata_with_missing(
        self,
        discovered_sessions,
        subject_lab_mapping,
    ):
        rdss_rows = self._list_rdss_metadata_rows()
        rows_by_lab = {}
        for row in rdss_rows:
            lab_id = str(row.get("labID", ""))
            rows_by_lab.setdefault(lab_id, []).append(row)

        for lab_id in rows_by_lab:
            rows_by_lab[lab_id].sort(
                key=lambda row: (
                    self._normalize_record_date_value(row.get("date")) or "",
                    str(row.get("filename", "")),
                )
            )

        enriched = {}
        missing = []

        for subject_id, sessions in (discovered_sessions or {}).items():
            subject_key = str(subject_id)
            mapped_lab_id = str((subject_lab_mapping or {}).get(subject_key, ""))
            if not mapped_lab_id:
                continue

            lab_rows = rows_by_lab.get(mapped_lab_id, [])

            for session in sorted(sessions or [], key=lambda item: int(item.get("run", 0))):
                run = int(session.get("run"))
                if run <= 0 or run > len(lab_rows):
                    missing.append(
                        {
                            "subject_id": subject_key,
                            "run": run,
                            "labID": mapped_lab_id,
                        }
                    )
                    continue

                selected = lab_rows[run - 1]
                if not all(selected.get(field) for field in ("filename", "labID", "date")):
                    missing.append(
                        {
                            "subject_id": subject_key,
                            "run": run,
                            "labID": mapped_lab_id,
                        }
                    )
                    continue

                enriched.setdefault(subject_key, []).append(
                    {
                        "filename": selected["filename"],
                        "labID": selected["labID"],
                        "date": selected["date"],
                        "run": run,
                        "study": session.get("study"),
                        "file_path": session.get("file_path"),
                    }
                )

        for subject_id in enriched:
            enriched[subject_id].sort(key=lambda item: int(item["run"]))

        return enriched, missing

    def _session_run_from_folder(self, session_folder_name):
        match = re.fullmatch(r"ses-(\d+)", str(session_folder_name or ""))
        if not match:
            return None
        return int(match.group(1))

    def _candidate_session_csvs(self, session_dir):
        if not os.path.isdir(session_dir):
            return []

        candidates = []
        for name in sorted(os.listdir(session_dir)):
            lower_name = name.lower()
            if lower_name.endswith("_accel.csv"):
                candidates.append(os.path.join(session_dir, name))
        return candidates

    def _validate_session_csv_candidate(self, session_dir):
        candidates = self._candidate_session_csvs(session_dir)
        if len(candidates) > 1:
            raise ValueError(
                f"multiple accel csv candidates in {session_dir}: "
                + ", ".join(os.path.basename(path) for path in candidates)
            )
        if not candidates:
            return None
        return candidates[0]

    def _file_size(self, path):
        return os.path.getsize(path)

    def _file_sha256(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _compare_file_identity(self, source_path, destination_path):
        source_size = self._file_size(source_path)
        destination_size = self._file_size(destination_path)
        size_match = source_size == destination_size
        hash_match = False
        if size_match:
            hash_match = self._file_sha256(source_path) == self._file_sha256(
                destination_path
            )

        return {
            "size_match": size_match,
            "hash_match": hash_match,
            "match": size_match and hash_match,
        }

    def _new_reconcile_report(self):
        return {
            "total_records": 0,
            "repaired": 0,
            "mismatched": 0,
            "missing_source": 0,
            "missing_dest": 0,
            "ambiguous_dest": 0,
            "errors": [],
        }

    def _record_reconcile_error(self, report, status, message):
        report[status] = report.get(status, 0) + 1
        report.setdefault("errors", []).append(message)

    def _replace_file_atomically(self, source_path, destination_path):
        destination_dir = os.path.dirname(destination_path) or "."
        os.makedirs(destination_dir, exist_ok=True)

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=".reconcile-",
                suffix=".csv",
                dir=destination_dir,
            )
            with os.fdopen(fd, "wb") as handle, open(source_path, "rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                shutil.copystat(source_path, temp_path, follow_symlinks=True)
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.EACCES):
                    raise
                self.logger.warning(
                    "reconcile_copystat_skipped source=%s destination=%s error=%s",
                    source_path,
                    destination_path,
                    exc,
                )
            os.replace(temp_path, destination_path)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

    def reconcile_manifest(self):
        manifest = self._load_manifest(getattr(self, "manifest_path", "res/data.json"))
        report = self._new_reconcile_report()

        for subject_id in sorted(manifest.keys(), key=self._subject_order_key):
            records = manifest.get(subject_id, [])
            for record in sorted(records, key=lambda item: int(item.get("run", 0))):
                report["total_records"] += 1

                run = int(record.get("run", 0))
                source_path = os.path.join(self.RDSS_DIR, str(record.get("filename", "")))
                destination_path = str(record.get("file_path", ""))
                session_dir = os.path.dirname(destination_path)
                log_context = (
                    f"subject={subject_id} run={run} source={source_path} "
                    f"destination={destination_path}"
                )

                try:
                    candidate = self._validate_session_csv_candidate(session_dir)
                except ValueError as exc:
                    self.logger.error("reconcile_failed %s error=%s", log_context, exc)
                    self._record_reconcile_error(
                        report,
                        "ambiguous_dest",
                        f"{log_context} error={exc}",
                    )
                    continue

                if not os.path.exists(source_path):
                    self.logger.error("reconcile_failed %s error=missing_source", log_context)
                    self._record_reconcile_error(
                        report,
                        "missing_source",
                        f"{log_context} error=missing_source",
                    )
                    continue

                if not destination_path or not os.path.exists(destination_path):
                    self.logger.error("reconcile_failed %s error=missing_dest", log_context)
                    self._record_reconcile_error(
                        report,
                        "missing_dest",
                        f"{log_context} error=missing_dest",
                    )
                    continue

                if candidate != destination_path:
                    self.logger.error(
                        "reconcile_failed %s error=missing_dest candidate=%s",
                        log_context,
                        candidate,
                    )
                    self._record_reconcile_error(
                        report,
                        "missing_dest",
                        f"{log_context} error=missing_dest candidate={candidate}",
                    )
                    continue

                identity = self._compare_file_identity(source_path, destination_path)
                if identity["match"]:
                    self.logger.info("reconcile_ok %s", log_context)
                    continue

                report["mismatched"] += 1
                self.logger.warning(
                    "reconcile_mismatch %s size_match=%s hash_match=%s",
                    log_context,
                    identity["size_match"],
                    identity["hash_match"],
                )

                try:
                    self._replace_file_atomically(source_path, destination_path)
                except Exception as exc:
                    self.logger.error("reconcile_failed %s error=%s", log_context, exc)
                    report.setdefault("errors", []).append(
                        f"{log_context} error={exc}"
                    )
                    continue

                report["repaired"] += 1
                if self.symlink:
                    self._refresh_subject_symlinks(destination_path)
                self.logger.info("reconcile_repaired %s", log_context)

        return report

    def discover_lss_sessions(self):
        discovered = {}
        conflicts = {}

        for study, study_root in (("int", self.INT_DIR), ("obs", self.OBS_DIR)):
            if not study_root or not os.path.isdir(study_root):
                continue

            for subject_folder in sorted(os.listdir(study_root)):
                if not subject_folder.startswith("sub-"):
                    continue

                subject_id = subject_folder[len("sub-"):]
                accel_root = os.path.join(study_root, subject_folder, "accel")
                if not os.path.isdir(accel_root):
                    continue

                for session_folder in sorted(os.listdir(accel_root)):
                    run = self._session_run_from_folder(session_folder)
                    if run is None:
                        continue

                    session_dir = os.path.join(accel_root, session_folder)
                    if not os.path.isdir(session_dir):
                        continue

                    try:
                        csv_path = self._validate_session_csv_candidate(session_dir)
                    except ValueError as exc:
                        conflict_message = str(exc)
                        conflicts.setdefault(subject_id, []).append(conflict_message)
                        continue

                    if csv_path is None:
                        continue

                    discovered.setdefault(subject_id, []).append(
                        {
                            "subject_id": subject_id,
                            "study": study,
                            "run": run,
                            "file_path": csv_path,
                            "filename": os.path.basename(csv_path),
                        }
                    )

        for subject_id in discovered:
            discovered[subject_id].sort(key=lambda record: record["run"])

        return discovered, conflicts

    def _list_rdss_metadata_rows(self):
        rdss_dir = getattr(self, "RDSS_DIR", None)
        if not rdss_dir or not os.path.isdir(rdss_dir):
            return []

        rows = []
        pattern = re.compile(r"^(?P<lab_id>\S+)\s*\((?P<date>[^)]+)\).+\.csv$", re.IGNORECASE)

        for filename in sorted(os.listdir(rdss_dir)):
            if not filename.lower().endswith(".csv"):
                continue

            match = pattern.match(filename)
            if not match:
                continue

            rows.append(
                {
                    "filename": filename,
                    "labID": str(match.group("lab_id")),
                    "date": self._normalize_record_date_value(match.group("date")),
                }
            )

        rows.sort(
            key=lambda row: (
                self._normalize_record_date_value(row.get("date")) or "",
                str(row.get("filename", "")),
            )
        )
        return rows

    def resolve_rdss_session_metadata(self, discovered_sessions, subject_lab_mapping):
        enriched, missing = self._resolve_rdss_session_metadata_with_missing(
            discovered_sessions,
            subject_lab_mapping,
        )

        if missing:
            raise ValueError(
                "Unresolved RDSS metadata for session(s): "
                + "; ".join(
                    sorted(
                        f"subject={entry['subject_id']} run={entry['run']} labID={entry['labID']}"
                        for entry in missing
                    )
                )
            )

        return enriched

    def rebuild_manifest_payload_from_lss(self):
        discovered, discovery_conflicts = self.discover_lss_sessions()

        subject_errors = {}
        for subject_id, messages in (discovery_conflicts or {}).items():
            subject_errors.setdefault(str(subject_id), []).extend(list(messages or []))

        subject_ids = sorted(discovered.keys(), key=self._subject_order_key)

        report_rows = self._fetch_redcap_subject_lab_rows()
        if hasattr(report_rows, "to_dict"):
            report_rows = report_rows.to_dict(orient="records")

        subject_lab_mapping, missing_subjects = self._build_subject_lab_mapping(
            subject_ids,
            report_rows,
        )
        for subject_id in missing_subjects:
            if self._subject_exempt_from_manifest_metadata_strictness(subject_id):
                continue
            subject_errors.setdefault(subject_id, []).append(
                "missing RedCap subject->lab mapping"
            )

        discovered_with_mapping = {
            subject_id: discovered[subject_id]
            for subject_id in subject_ids
            if subject_id in subject_lab_mapping
        }
        enriched, rdss_missing = self._resolve_rdss_session_metadata_with_missing(
            discovered_with_mapping,
            subject_lab_mapping,
        )
        for item in rdss_missing:
            subject_id = str(item["subject_id"])
            subject_errors.setdefault(subject_id, []).append(
                f"unresolved RDSS metadata for run={item['run']} labID={item['labID']}"
            )

        if subject_errors:
            parts = []
            for subject_id in sorted(subject_errors.keys(), key=self._subject_order_key):
                messages = "; ".join(sorted(set(subject_errors[subject_id])))
                parts.append(f"subject={subject_id}: {messages}")
            raise ValueError(
                "Manifest rebuild failed due to strict conflict(s): " + " | ".join(parts)
            )

        payload = {}
        for subject_id in sorted(enriched.keys(), key=self._subject_order_key):
            records = sorted(enriched[subject_id], key=lambda row: int(row["run"]))
            payload[str(subject_id)] = records

        return self._prepare_for_json(payload)

    def _subject_session_paths(self, subject_id, study, run):
        subject_key = str(subject_id)
        session = int(run)
        study_root = self._subject_study_root(study)
        session_dir = os.path.join(
            study_root,
            f"sub-{subject_key}",
            "accel",
            f"ses-{session}",
        )
        session_file = os.path.join(
            session_dir,
            f"sub-{subject_key}_ses-{session}_accel.csv",
        )
        return session_dir, session_file

    def _infer_subject_study(self, subject_id, fallback_records=None):
        if fallback_records:
            for record in fallback_records:
                study = (record or {}).get("study")
                if study in {"int", "obs"}:
                    return study

        try:
            subject_val = int(subject_id)
        except (TypeError, ValueError):
            return "obs"

        if subject_val >= 8000:
            return "int"
        return "obs"

    def _copy_subject_record(self, record):
        source_path = os.path.join(self.RDSS_DIR, record["filename"])
        destination_path = record["file_path"]
        subject_id = str(record.get("subject_id", ""))
        run = record.get("run")
        log_context = (
            f"subject={subject_id} run={run} source={source_path} "
            f"destination={destination_path}"
        )

        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source file not found for {log_context}")

        destination_dir = os.path.dirname(destination_path)
        os.makedirs(destination_dir, exist_ok=True)

        if os.path.exists(destination_path):
            identity = self._compare_file_identity(source_path, destination_path)
            if not identity["match"]:
                raise ValueError(
                    "Destination identity mismatch for "
                    f"{log_context} size_match={identity['size_match']} "
                    f"hash_match={identity['hash_match']}"
                )
            self.logger.info("skip_existing %s", log_context)
            if self.symlink:
                self._refresh_subject_symlinks(destination_path)
            return None

        shutil.copy2(source_path, destination_path)
        self.logger.info("copied %s", log_context)
        if self.symlink:
            self._refresh_subject_symlinks(destination_path)
        return destination_path

    def _rollback_rename_plan(self, rename_plan):
        inverse_moves = []
        for move in reversed((rename_plan or {}).get("moves", [])):
            inverse_moves.append(
                {
                    "record_key": move.get("record_key"),
                    "old_run": move.get("new_run"),
                    "new_run": move.get("old_run"),
                    "old_dir": move.get("new_dir"),
                    "new_dir": move.get("old_dir"),
                    "old_file": move.get("new_file"),
                    "new_file": move.get("old_file"),
                }
            )

        inverse_plan = {
            "subject_id": (rename_plan or {}).get("subject_id"),
            "study": (rename_plan or {}).get("study"),
            "moves": inverse_moves,
        }

        if inverse_moves:
            self._apply_two_phase_renames(inverse_plan)

    def _process_subject_transaction(self, subject_id, incoming_records):
        subject_key = str(subject_id)
        incoming_records = incoming_records or []
        existing_records = [dict(record) for record in self.manifest.get(subject_key, [])]
        merged_records = self._reindex_subject_records(existing_records, incoming_records)

        if self._detect_same_date_conflict(merged_records):
            self.logger.warning("skip_tie_date subject=%s", subject_key)
            return []

        merged_records.sort(key=self._subject_sort_key)
        subject_study = self._infer_subject_study(subject_key, incoming_records)

        canonical_records = []
        canonical_lookup = {}
        for idx, record in enumerate(merged_records, start=1):
            canonical = dict(record)
            canonical["subject_id"] = subject_key
            canonical["run"] = idx
            canonical["study"] = subject_study
            _, canonical_file = self._subject_session_paths(subject_key, subject_study, idx)
            canonical["file_path"] = canonical_file
            canonical_records.append(canonical)
            canonical_lookup[self._record_identity_key(canonical)] = canonical

        existing_keys = {
            self._record_identity_key(record)
            for record in existing_records
            if isinstance(record, dict)
        }
        incoming_keys = {
            self._record_identity_key(record)
            for record in incoming_records
            if isinstance(record, dict)
        }
        new_keys = incoming_keys - existing_keys

        old_dates = [
            self._normalize_record_date_value(record.get("date"))
            for record in existing_records
            if isinstance(record, dict)
        ]
        new_dates = [key[1] for key in new_keys if key[1] is not None]

        rename_plan = self._plan_subject_renames(
            subject_id=subject_key,
            study=subject_study,
            old_records=existing_records,
            new_records=canonical_records,
        )

        self._log_subject_file_plan(
            subject_id=subject_key,
            new_keys=new_keys,
            canonical_lookup=canonical_lookup,
            rename_plan=rename_plan,
        )

        if not new_keys and not rename_plan["moves"]:
            self.logger.info("noop_duplicate subject=%s", subject_key)

        if new_keys and old_dates and new_dates:
            if min(new_dates) > max(old_dates):
                self.logger.info("append_latest subject=%s", subject_key)
            else:
                self.logger.info("backfill_reindex subject=%s", subject_key)

        applied_renames = False
        copied_paths = []
        try:
            self._apply_two_phase_renames(rename_plan)
            applied_renames = bool(rename_plan["moves"])

            for record_key in new_keys:
                canonical_record = canonical_lookup.get(record_key)
                if canonical_record is None:
                    continue
                copied_path = self._copy_subject_record(canonical_record)
                if copied_path:
                    copied_paths.append(copied_path)

            self.manifest[subject_key] = canonical_records

            committed_records = []
            for record in incoming_records:
                mapped = canonical_lookup.get(self._record_identity_key(record))
                if mapped is not None:
                    committed_records.append(dict(mapped))

            committed_records.sort(key=self._subject_sort_key)
            return committed_records
        except Exception as exc:
            self.logger.warning("rename_failed subject=%s error=%s", subject_key, exc)

            for copied_path in copied_paths:
                try:
                    if os.path.exists(copied_path):
                        os.remove(copied_path)
                except OSError:
                    pass

            if applied_renames:
                try:
                    self._rollback_rename_plan(rename_plan)
                except Exception as rollback_exc:
                    self.logger.warning(
                        "rename_failed subject=%s rollback_error=%s",
                        subject_key,
                        rollback_exc,
                    )

            return []

    def _plan_subject_renames(self, subject_id, study, old_records, new_records):
        old_by_key = {
            self._record_identity_key(record): record
            for record in (old_records or [])
            if isinstance(record, dict)
        }
        new_by_key = {
            self._record_identity_key(record): record
            for record in (new_records or [])
            if isinstance(record, dict)
        }

        rename_steps = []
        for record_key, old_record in old_by_key.items():
            new_record = new_by_key.get(record_key)
            if new_record is None:
                continue

            old_run = old_record.get("run")
            new_run = new_record.get("run")
            if old_run is None or new_run is None or int(old_run) == int(new_run):
                continue

            old_dir, old_file = self._subject_session_paths(subject_id, study, old_run)
            new_dir, new_file = self._subject_session_paths(subject_id, study, new_run)

            rename_steps.append(
                {
                    "record_key": record_key,
                    "old_run": int(old_run),
                    "new_run": int(new_run),
                    "old_dir": old_dir,
                    "new_dir": new_dir,
                    "old_file": old_file,
                    "new_file": new_file,
                }
            )

        return {
            "subject_id": str(subject_id),
            "study": (study or "").lower(),
            "moves": rename_steps,
        }

    def _log_subject_file_plan(self, subject_id, new_keys, canonical_lookup, rename_plan):
        subject_key = str(subject_id)

        planned_new_records = []
        for record_key in new_keys:
            record = canonical_lookup.get(record_key)
            if record is not None:
                planned_new_records.append(record)

        planned_new_records.sort(key=self._subject_sort_key)

        if planned_new_records:
            self.logger.info(
                "planned_new_files subject=%s count=%s",
                subject_key,
                len(planned_new_records),
            )
            for record in planned_new_records:
                source_path = os.path.join(self.RDSS_DIR, str(record.get("filename", "")))
                self.logger.info(
                    "planned_save subject=%s run=%s source=%s destination=%s",
                    subject_key,
                    record.get("run"),
                    source_path,
                    record.get("file_path"),
                )

        planned_switches = (rename_plan or {}).get("moves", [])
        if planned_switches:
            self.logger.info(
                "planned_file_switches subject=%s count=%s",
                subject_key,
                len(planned_switches),
            )
            for move in sorted(
                planned_switches,
                key=lambda item: (item.get("old_run"), item.get("new_run")),
            ):
                self.logger.info(
                    "planned_switch subject=%s from_run=%s to_run=%s from=%s to=%s",
                    subject_key,
                    move.get("old_run"),
                    move.get("new_run"),
                    move.get("old_file"),
                    move.get("new_file"),
                )

    def _apply_two_phase_renames(self, rename_plan):
        moves = (rename_plan or {}).get("moves", [])
        if not moves:
            return []

        temp_hops = []
        moved_to_temp = []
        moved_to_final = []

        try:
            for index, move in enumerate(moves, start=1):
                old_dir = move["old_dir"]
                if not os.path.exists(old_dir):
                    continue

                self._validate_session_csv_candidate(old_dir)

                base_parent = os.path.dirname(old_dir)
                temp_dir = os.path.join(
                    base_parent,
                    f".tmp-{rename_plan.get('subject_id', 'subject')}-{index}-{move['old_run']}-to-{move['new_run']}",
                )
                while os.path.exists(temp_dir):
                    temp_dir = f"{temp_dir}-x"

                os.makedirs(os.path.dirname(temp_dir), exist_ok=True)
                os.rename(old_dir, temp_dir)

                hop = {
                    "temp_dir": temp_dir,
                    "old_dir": move["old_dir"],
                    "new_dir": move["new_dir"],
                    "old_file": move["old_file"],
                    "new_file": move["new_file"],
                }
                temp_hops.append(hop)
                moved_to_temp.append(hop)

            for hop in temp_hops:
                new_dir = hop["new_dir"]
                new_file = hop["new_file"]
                temp_dir = hop["temp_dir"]

                os.makedirs(os.path.dirname(new_dir), exist_ok=True)
                os.rename(temp_dir, new_dir)
                moved_to_final.append(hop)

                current_file = self._validate_session_csv_candidate(new_dir)

                if current_file and current_file != new_file:
                    if os.path.exists(new_file):
                        os.remove(new_file)
                    os.rename(current_file, new_file)
        except Exception:
            for hop in reversed(moved_to_final):
                try:
                    if os.path.exists(hop["new_dir"]):
                        os.rename(hop["new_dir"], hop["old_dir"])
                    old_dir = hop["old_dir"]
                    old_file = hop["old_file"]
                    current_file = None
                    if os.path.isdir(old_dir):
                        try:
                            current_file = self._validate_session_csv_candidate(old_dir)
                        except ValueError:
                            current_file = None
                    if current_file and current_file != old_file:
                        if os.path.exists(old_file):
                            os.remove(old_file)
                        os.rename(current_file, old_file)
                except OSError:
                    pass

            for hop in reversed(moved_to_temp):
                try:
                    if os.path.exists(hop["temp_dir"]):
                        os.rename(hop["temp_dir"], hop["old_dir"])
                except OSError:
                    pass
            raise

        return temp_hops

    def _move_files_test(self, matches):
        """
        Moves only one file per study category ('int' and 'obs') from RDSS_DIR to the determined file_path.
        If the destination file already exists, it skips the move.
        """
        selected_files = {"int": None, "obs": None}

        for subject_id, records in matches.items():
            for record in records:
                study = record.get("study")
                if study in selected_files and selected_files[study] is None:
                    selected_files[study] = record  # Store first occurrence

                # Stop once we have one file per category
                if all(selected_files.values()):
                    break
            if all(selected_files.values()):
                break

        for study, record in selected_files.items():
            if record:
                source_path = os.path.join(self.RDSS_DIR, record["filename"])
                destination_path = record["file_path"]

                if not os.path.exists(source_path):
                    print(f"Source file not found: {source_path}. Skipping.")
                    continue

                destination_dir = os.path.dirname(destination_path)
                os.makedirs(destination_dir, exist_ok=True)

                if os.path.exists(destination_path):
                    print(
                        f"File already exists at destination: {destination_path}. Skipping."
                    )
                else:
                    try:
                        shutil.copy(source_path, destination_path)
                        print(f"Copied {source_path} -> {destination_path}")
                    except Exception as e:
                        print(f"Error moving {source_path} to {destination_path}: {e}")
                        continue
                if self.symlink:
                    self._refresh_subject_symlinks(destination_path)

    def _move_files(self, matches):
        """
        Moves files from RDSS_DIR to the determined file_path in the matches dictionary.
        If the destination file already exists, it skips the move.

        Args:
            matches (dict): Dictionary where keys are subject IDs, and values are lists of dicts
                            containing 'filename' and 'file_path'.

        Returns:
            None
        """
        for subject_id, records in matches.items():
            for record in records:
                source_path = os.path.join(self.RDSS_DIR, record["filename"])
                destination_path = record["file_path"]

                if not os.path.exists(source_path):
                    print(f"Source file not found: {source_path}. Skipping.")
                    continue

                # Ensure the destination directory exists
                destination_dir = os.path.dirname(destination_path)
                os.makedirs(destination_dir, exist_ok=True)

                if os.path.exists(destination_path):
                    print(
                        f"File already exists at destination: {destination_path}. Skipping."
                    )
                    if self.symlink:
                        self._refresh_subject_symlinks(destination_path)
                    continue

                try:
                    shutil.copy(source_path, destination_path)
                    print(f"Moved {source_path} -> {destination_path}")
                except Exception as e:
                    print(f"Error moving {source_path} to {destination_path}: {e}")
                    continue

                if self.symlink:
                    self._refresh_subject_symlinks(destination_path)

    def _refresh_subject_symlinks(self, csv_path):
        """
        Ensure sub-*/accel/all contains symlinks for every CSV under the accel tree.

        Args:
            csv_path (str): Absolute path to the CSV that was just copied or confirmed.
        """
        session_dir = os.path.dirname(csv_path)
        subject_accel_dir = os.path.dirname(session_dir)

        if not os.path.isdir(subject_accel_dir):
            return

        csv_records = []
        for root, dirs, files in os.walk(subject_accel_dir):
            dirs[:] = [d for d in dirs if d != "all"]

            for name in files:
                if not name.lower().endswith(".csv"):
                    continue
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, subject_accel_dir)
                csv_records.append((full_path, rel_path))

        all_dir = os.path.join(subject_accel_dir, "all")
        if os.path.exists(all_dir):
            shutil.rmtree(all_dir)

        os.makedirs(all_dir, exist_ok=True)

        use_symlinks = True
        for src, rel_path in csv_records:
            rel_dir = os.path.dirname(rel_path)
            target_dir = (
                all_dir if rel_dir in ("", ".") else os.path.join(all_dir, rel_dir)
            )
            os.makedirs(target_dir, exist_ok=True)
            link_path = os.path.join(target_dir, os.path.basename(rel_path))

            if use_symlinks:
                try:
                    os.symlink(src, link_path)
                    continue
                except FileExistsError:
                    os.unlink(link_path)
                    os.symlink(src, link_path)
                    continue
                except OSError as exc:
                    if exc.errno not in (errno.EOPNOTSUPP, errno.EPERM, errno.EACCES):
                        raise
                    use_symlinks = False
                    self.logger.warning(
                        "Symlinks are not supported in %s; copying CSVs into accel/all instead.",
                        subject_accel_dir,
                    )

            if os.path.exists(link_path):
                os.unlink(link_path)
            shutil.copy2(src, link_path)

    def _determine_run(self, matches):
        """
        Adds a 'run' key to the matches dictionary based on the chronological order of entries for each boost_id.

        Logic:
        - If a boost_id occurs multiple times in the matches list, assign a 'run' value in order of oldest to newest.
        The oldest entry is assigned 1, the next is 2, and so on.

        Args:
            matches (dict): Dictionary with keys as boost_ids and values as lists of dictionaries
                            containing 'filename', 'labID', 'date', and optionally other keys.

        Returns:
            dict: Updated matches dictionary with the 'run' key added to each entry.
        """
        for boost_id, incoming_records in matches.items():
            subject_id = str(boost_id)
            existing_records = self.manifest.get(subject_id, [])
            merged_records = self._reindex_subject_records(
                existing_records=existing_records,
                incoming_records=incoming_records,
            )

            if self._detect_same_date_conflict(merged_records):
                self.logger.warning(
                    "skip_tie_date subject=%s",
                    subject_id,
                )
                matches[boost_id] = []
                continue

            merged_records.sort(key=self._subject_sort_key)
            run_lookup = {}
            for idx, record in enumerate(merged_records, start=1):
                record["run"] = idx
                dedupe_key = (
                    str(record.get("labID", "")),
                    self._normalize_record_date_value(record.get("date")),
                    str(record.get("filename", "")),
                )
                run_lookup[dedupe_key] = idx

            reconciled_records = []
            seen_incoming_keys = set()
            for record in incoming_records:
                if not isinstance(record, dict):
                    continue

                reconciled = dict(record)
                reconciled["date"] = self._normalize_record_date_value(
                    reconciled.get("date")
                )
                dedupe_key = (
                    str(reconciled.get("labID", "")),
                    reconciled.get("date"),
                    str(reconciled.get("filename", "")),
                )

                if dedupe_key in seen_incoming_keys:
                    continue
                seen_incoming_keys.add(dedupe_key)

                assigned_run = run_lookup.get(dedupe_key)
                if assigned_run is None:
                    continue

                reconciled["run"] = assigned_run
                reconciled_records.append(reconciled)

            reconciled_records.sort(key=self._subject_sort_key)
            matches[boost_id] = reconciled_records

        return matches

    # make sure this pushes??
    def _determine_study(self, matches):
        """
        Adds a 'study' key to the matches dictionary based on the boost_id.

        Logic:
        - If boost_id > 7000 and < 8000, 'study' = 'obs'
        - If boost_id >= 8000, 'study' = 'int'

        Args:
            matches (dict): Dictionary with keys as boost_ids and values as lists of dictionaries
                            containing 'filename', 'labID', and 'date'.

        Returns:
            dict: Updated matches dictionary with the 'study' key added to each entry.
        """
        for boost_id, match_list in matches.items():
            # Ensure boost_id is an integer for comparison
            try:
                boost_id_int = int(boost_id)
            except ValueError:
                raise ValueError(f"Invalid boost_id format: {boost_id}")

            # Determine the study type based on the boost_id
            if 6000 < boost_id_int < 8000:
                study = "obs"
            elif boost_id_int >= 8000:
                study = "int"
            else:
                study = None  # Or a default value if needed

            # Append the study type to each match dictionary
            for match in match_list:
                match["study"] = study

        return matches

    def _determine_location(self, matches):
        """
        STRUCTURE OF DIRECTORIES
        study folder (Intervention / Observation)
                |-> bids
                    |-> sub-####
                        |-> accel
                            |-> sub-####_ses-#_accel.csv
        """
        for subject_id, records in matches.items():
            try:
                for record in records:
                    if record["study"] is None:
                        raise TypeError(f"study is None for {subject_id}")

                    study_dir = (
                        self.INT_DIR
                        if record["study"].lower() == "int"
                        else self.OBS_DIR
                    )
                    subject_folder = f"sub-{subject_id}"
                    session = record[
                        "run"
                    ]  # 'run' is synonymous with 'session' or 'set'
                    filename = f"sub-{subject_id}_ses-{session}_accel.csv"

                    # Construct full path
                    file_path = (
                        f"{study_dir}/{subject_folder}/accel/ses-{session}/{filename}"
                    )

                    # Append file path to record
                    record["file_path"] = file_path

            except TypeError as e:
                self.logger.warning("Skipping subject %s due to error: %s", subject_id, e)
                continue  # Skip this subject and move to the next one

        return matches

    def _prepare_for_json(self, matches):
        """Convert non-serializable values (e.g., pandas Timestamps) to strings."""
        for records in matches.values():
            for record in records:
                date_value = record.get("date")
                if date_value is None or isinstance(date_value, str):
                    continue
                if hasattr(date_value, "isoformat"):
                    record["date"] = date_value.isoformat()
                else:
                    record["date"] = str(date_value)
        return matches

    @staticmethod
    def remove_symlink_directories(study_dirs):
        """
        Remove per-subject accel/all directories that only contain symlinks created during ingest.

        Args:
            study_dirs (Iterable[str]): Iterable of study root paths to inspect.
        """
        for base_dir in study_dirs:
            if not base_dir or not os.path.isdir(base_dir):
                continue

            try:
                subjects = os.listdir(base_dir)
            except OSError:
                continue

            for subject in subjects:
                accel_all_dir = os.path.join(base_dir, subject, "accel", "all")

                if os.path.islink(accel_all_dir):
                    try:
                        os.unlink(accel_all_dir)
                    except OSError as exc:
                        print(f"Unable to unlink {accel_all_dir}: {exc}")
                    continue

                if os.path.isdir(accel_all_dir):
                    try:
                        shutil.rmtree(accel_all_dir)
                    except OSError as exc:
                        print(f"Unable to remove directory {accel_all_dir}: {exc}")

    def _handle_and_merge_duplicates(self, duplicates):
        """
        Processes duplicate entries and merges them into the main matches dictionary.

        For each lab_id group in the duplicates (each group is expected to contain two entries:
        one with a boost_id < 8000 for the observational study and one with a boost_id >= 8000 for
        the interventional study):

        1. Combine the filenames and dates from both entries and sort them chronologically.
        2. Pre-build the expected observational study session-1 path:
                OBS_DIR/sub-<obs_boost_id>/accel/ses-1/sub-<obs_boost_id>_ses-1_accel.csv
            (Here the subject folder is built using the observational study ID, which must be less than 8000.)
        3. If that OBS session-1 file does not exist:
                - Assign the earliest file (by date) as observational (study = 'obs', run = 1)
                using the obs boost_id.
                - Assign any remaining files as interventional (study = 'int') with run numbers
                starting at 1 and using the int boost_id.
            If the OBS session-1 file does exist:
                - Assign all files to the interventional study (study = 'int') with run numbers
                starting at 1, using the int boost_id.
        4. Merge these processed duplicate entries into the main matches dictionary. Each entry
            is stored under the key equal to its subject_id.

        If an expected observational (<8000) or interventional (>=8000) ID is missing for a group,
        an error is raised and processing is stopped.

        Args:
            duplicates (list): A list of dictionaries with keys 'lab_id', 'boost_id', 'filenames', and 'dates'
                            representing duplicate records.

        Returns:
            dict: The updated matches dictionary with processed duplicates merged in.
        """
        # Group duplicates by lab_id.
        grouped = {}
        for dup in duplicates:
            lab_id = dup["lab_id"]
            grouped.setdefault(lab_id, []).append(dup)

        # Process each group.
        for lab_id, dup_list in grouped.items():
            # Identify the observational and interventional entries.
            obs_entry = None
            int_entry = None
            for entry in dup_list:
                try:
                    boost_val = int(entry["boost_id"])
                except ValueError:
                    raise ValueError(
                        f"Invalid boost_id in duplicates for lab_id {lab_id}: {entry['boost_id']}"
                    )
                if boost_val < 8000:
                    obs_entry = entry
                elif boost_val >= 8000:
                    int_entry = entry

            if obs_entry is None:
                raise ValueError(
                    f"Missing observational study ID (boost_id < 8000) for lab_id {lab_id}."
                )
            if int_entry is None:
                raise ValueError(
                    f"Missing interventional study ID (boost_id >= 8000) for lab_id {lab_id}."
                )

            # Combine filenames and dates from both entries.
            combined = []
            for fname, fdate in zip(obs_entry["filenames"], obs_entry["dates"]):
                combined.append((fname, fdate))
            for fname, fdate in zip(int_entry["filenames"], int_entry["dates"]):
                combined.append((fname, fdate))
            # Sort by date (assuming dates are comparable)
            combined.sort(key=lambda x: x[1])

            # Determine subject IDs from the entries.
            obs_boost_id = str(obs_entry["boost_id"])
            int_boost_id = str(int_entry["boost_id"])

            # Build the expected OBS session 1 path (canonical ses-* layout).
            subject_folder_obs = f"sub-{obs_boost_id}"
            obs_session1_path = os.path.join(
                self.OBS_DIR,
                subject_folder_obs,
                "accel",
                "ses-1",
                f"sub-{obs_boost_id}_ses-1_accel.csv",
            )

            new_entries = []
            if not os.path.exists(obs_session1_path):
                # OBS session 1 does not exist.
                # The first (oldest) file becomes observational.
                first_fname, first_date = combined[0]
                new_entries.append(
                    {
                        "filename": first_fname,
                        "labID": lab_id,
                        "date": first_date,
                        "study": "obs",
                        "run": 1,
                        "file_path": obs_session1_path,
                        "subject_id": obs_boost_id,
                    }
                )
                # All remaining files become interventional (run numbers start at 2).
                for idx, (fname, fdate) in enumerate(combined[1:], start=1):
                    subject_folder_int = f"sub-{int_boost_id}"
                    int_file_path = os.path.join(
                        self.INT_DIR,
                        subject_folder_int,
                        "accel",
                        f"sub-{int_boost_id}_ses-{idx}_accel.csv",
                    )
                    new_entries.append(
                        {
                            "filename": fname,
                            "labID": lab_id,
                            "date": fdate,
                            "study": "int",
                            "run": idx,
                            "file_path": int_file_path,
                            "subject_id": int_boost_id,
                        }
                    )
            else:
                # OBS session 1 exists; assign all duplicate files as interventional.
                for idx, (fname, fdate) in enumerate(combined, start=1):
                    subject_folder_int = f"sub-{int_boost_id}"
                    int_file_path = os.path.join(
                        self.INT_DIR,
                        subject_folder_int,
                        "accel",
                        f"sub-{int_boost_id}_ses-{idx}_accel.csv",
                    )
                    new_entries.append(
                        {
                            "filename": fname,
                            "labID": lab_id,
                            "date": fdate,
                            "study": "int",
                            "run": idx,
                            "file_path": int_file_path,
                            "subject_id": int_boost_id,
                        }
                    )

            # Merge the processed duplicate entries into the main matches dictionary.
            for entry in new_entries:
                subject_key = entry["subject_id"]
                if subject_key in self.matches:
                    self.matches[subject_key].append(entry)
                else:
                    self.matches[subject_key] = [entry]

        return self.matches
