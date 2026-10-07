#!/usr/bin/env python3
"""Fetch Strava activities or import local FIT files into analysis-ready files."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

CALLBACK_URL = "http://localhost:8000/callback"
TOKEN_URL = "https://www.strava.com/oauth/token"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
API_BASE = "https://www.strava.com/api/v3"
FULL_STREAM_KEYS = [
    "time", "distance", "latlng", "altitude", "velocity_smooth",
    "heartrate", "cadence", "watts", "temp", "moving", "grade_smooth",
]
TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
TCX_EXT_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
FIELDS = [
    "id", "name", "type", "sport_type", "start_date", "start_date_local",
    "timezone", "distance", "moving_time", "elapsed_time",
    "total_elevation_gain", "elev_high", "elev_low", "average_speed",
    "max_speed", "average_heartrate", "max_heartrate", "average_watts",
    "max_watts", "average_cadence", "kilojoules", "device_watts", "trainer", "commute",
    "manual", "gear_id", "achievement_count", "kudos_count",
    "comment_count", "photo_count", "suffer_score",
]


def token_path() -> Path:
    """Keep OAuth credentials outside the synced project directory."""
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        base = Path(appdata)
    else:
        base = Path.home() / ".config"
    return base / "strava-analysis" / "tokens.json"


def data_dir() -> Path:
    return Path(__file__).resolve().parent / "strava_data"


def client_config_value(key: str, env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        secrets_path = Path(__file__).resolve().parent / "strava_secrets.json"
        if secrets_path.exists():
            try:
                settings = json.loads(secrets_path.read_text(encoding="utf-8"))
                value = str(settings.get(key) or "").strip()
            except (OSError, ValueError, AttributeError) as exc:
                raise RuntimeError(f"Cannot read {secrets_path.name}: {exc}") from exc
    if not value:
        raise RuntimeError(f"Set {env_name} or add {key} to strava_secrets.json.")
    return value


def client_id() -> str:
    return client_config_value("client_id", "STRAVA_CLIENT_ID")


def client_secret() -> str:
    return client_config_value("client_secret", "STRAVA_CLIENT_SECRET")


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None,
              access_token: str | None = None) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "local-strava-analysis/1.0"}
    body = None
    if payload is not None:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429:
            raise RuntimeError(
                "Strava rate limit reached. Completed activity files are saved; wait and rerun to resume."
            ) from exc
        raise RuntimeError(f"Strava returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Strava: {exc.reason}") from exc


class CallbackHandler(BaseHTTPRequestHandler):
    state_expected = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        received_state = query.get("state", [""])[0]
        if parsed.path != "/callback" or not secrets.compare_digest(
            received_state, self.state_expected
        ):
            self.send_error(400, "Invalid OAuth callback state")
            return
        if query.get("error"):
            self.result["error"] = query["error"][0]
        else:
            self.result["code"] = query.get("code", [""])[0]
        page = (
            "<!doctype html><meta charset='utf-8'><title>Strava 授权</title>"
            "<p>授权结果已收到。可以关闭此页面并返回终端。</p>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not log callback query strings, which contain the temporary code.
        return


def authorize() -> None:
    secret = client_secret()
    state = secrets.token_urlsafe(24)
    CallbackHandler.state_expected = state
    CallbackHandler.result = {}
    params = {
        "client_id": client_id(),
        "redirect_uri": CALLBACK_URL,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    server = HTTPServer(("127.0.0.1", 8000), CallbackHandler)
    server.timeout = 240
    print("Opening Strava authorization in your browser...")
    print(f"If it does not open, visit:\n{url}")
    webbrowser.open(url)
    try:
        while not CallbackHandler.result:
            server.handle_request()
            if server.timeout is not None:
                # handle_request returns on timeout; allow one four-minute window.
                if not CallbackHandler.result:
                    raise RuntimeError("Timed out waiting for Strava authorization.")
    finally:
        server.server_close()
    if CallbackHandler.result.get("error"):
        raise RuntimeError(f"Strava authorization failed: {CallbackHandler.result['error']}")
    code = CallbackHandler.result.get("code")
    if not code:
        raise RuntimeError("Strava callback did not include an authorization code.")
    token = http_json(
        TOKEN_URL,
        method="POST",
        payload={"client_id": client_id(), "client_secret": secret,
                 "code": code, "grant_type": "authorization_code"},
    )
    save_tokens(token)
    athlete = token.get("athlete") or {}
    label = " ".join(str(athlete.get(k, "")) for k in ("firstname", "lastname")).strip()
    print(f"Authorization saved for {label or 'your Strava account'}.")
    print(f"Credential file: {token_path()}")


def save_tokens(token: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_tokens() -> dict[str, Any]:
    path = token_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("No saved Strava authorization. Run: python strava_tool.py auth") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Token file is invalid: {path}") from exc


def valid_access_token() -> str:
    secret = client_secret()
    tokens = load_tokens()
    expires_at = int(tokens.get("expires_at", 0))
    if expires_at <= int(time.time()) + 60:
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise RuntimeError("Saved token has no refresh token. Run authorization again.")
        updated = http_json(
            TOKEN_URL,
            method="POST",
            payload={"client_id": client_id(), "client_secret": secret,
                     "refresh_token": refresh, "grant_type": "refresh_token"},
        )
        save_tokens(updated)
        tokens = updated
    access = tokens.get("access_token")
    if not access:
        raise RuntimeError("No access token found. Run authorization again.")
    return str(access)


def fetch_activities(after: int, access: str) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    page = 1
    while True:
        params = urllib.parse.urlencode({"after": after, "per_page": 200, "page": page})
        batch = http_json(f"{API_BASE}/athlete/activities?{params}", access_token=access)
        if not isinstance(batch, list):
            raise RuntimeError("Strava returned an unexpected activities response.")
        activities.extend(batch)
        if len(batch) < 200:
            break
        page += 1
    return activities


def stream_values(streams: dict[str, Any], key: str) -> list[Any]:
    stream = streams.get(key)
    if isinstance(stream, dict):
        values = stream.get("data", [])
        return values if isinstance(values, list) else []
    return stream if isinstance(stream, list) else []


def parse_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def format_datetime(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_number(parent: ET.Element, name: str, value: Any) -> None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        ET.SubElement(parent, f"{{{TCX_NS}}}{name}").text = str(value)


def build_tcx(
    record: dict[str, Any], path: Path, *, creator_name: str = "Strava API"
) -> None:
    """Write one TCX activity, including the available high-resolution streams."""
    activity = record.get("activity") or {}
    summary = record.get("summary") or {}
    start_value = activity.get("start_date") or summary.get("start_date")
    if not start_value:
        raise RuntimeError(f"Activity {summary.get('id')} has no start date; cannot write TCX.")
    start = parse_datetime(str(start_value))

    ET.register_namespace("", TCX_NS)
    ET.register_namespace("xsi", XSI_NS)
    ET.register_namespace("act", TCX_EXT_NS)
    q = lambda tag: f"{{{TCX_NS}}}{tag}"
    root = ET.Element(q("TrainingCenterDatabase"))
    root.set(f"{{{XSI_NS}}}schemaLocation", f"{TCX_NS} http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd")
    # Used by xsi:type on Creator; keeping it declared also makes the type clear to parsers.
    root.set("xmlns:tcx", TCX_NS)
    activities_el = ET.SubElement(root, q("Activities"))
    sport_type = str(activity.get("type") or summary.get("type") or "").lower()
    if "run" in sport_type:
        sport = "Running"
    elif any(word in sport_type for word in ("ride", "cycl", "bike")):
        sport = "Biking"
    else:
        sport = "Other"
    activity_el = ET.SubElement(activities_el, q("Activity"), {"Sport": sport})
    ET.SubElement(activity_el, q("Id")).text = format_datetime(start)
    name = activity.get("name") or summary.get("name")
    if name:
        ET.SubElement(activity_el, q("Notes")).text = str(name)

    elapsed = activity.get("elapsed_time")
    if elapsed is None:
        elapsed = summary.get("elapsed_time")
    distance = activity.get("distance")
    if distance is None:
        distance = summary.get("distance")
    lap = ET.SubElement(activity_el, q("Lap"), {"StartTime": format_datetime(start)})
    add_number(lap, "TotalTimeSeconds", elapsed)
    add_number(lap, "DistanceMeters", distance)
    lap_stats = [
        ("AverageHeartRateBpm", "average_heartrate"),
        ("MaximumHeartRateBpm", "max_heartrate"),
    ]
    for element_name, field in lap_stats:
        value = activity.get(field)
        if value is None:
            value = summary.get(field)
        if value is not None:
            hr = ET.SubElement(lap, q(element_name))
            ET.SubElement(hr, q("Value")).text = str(int(value))
    ET.SubElement(lap, q("Intensity")).text = "Active"
    ET.SubElement(lap, q("TriggerMethod")).text = "Manual"
    track = ET.SubElement(lap, q("Track"))

    streams = record.get("streams") or {}
    times = stream_values(streams, "time")
    distances = stream_values(streams, "distance")
    latlngs = stream_values(streams, "latlng")
    altitudes = stream_values(streams, "altitude")
    speeds = stream_values(streams, "velocity_smooth")
    heart_rates = stream_values(streams, "heartrate")
    cadences = stream_values(streams, "cadence")
    watts = stream_values(streams, "watts")
    for index, offset in enumerate(times):
        if not isinstance(offset, (int, float)) or not math.isfinite(offset):
            continue
        point = ET.SubElement(track, q("Trackpoint"))
        ET.SubElement(point, q("Time")).text = format_datetime(start + dt.timedelta(seconds=offset))
        if index < len(distances):
            add_number(point, "DistanceMeters", distances[index])
        if index < len(latlngs):
            coord = latlngs[index]
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                position = ET.SubElement(point, q("Position"))
                add_number(position, "LatitudeDegrees", coord[0])
                add_number(position, "LongitudeDegrees", coord[1])
        if index < len(altitudes):
            add_number(point, "AltitudeMeters", altitudes[index])
        if index < len(heart_rates) and isinstance(heart_rates[index], (int, float)):
            heart_rate = ET.SubElement(point, q("HeartRateBpm"))
            ET.SubElement(heart_rate, q("Value")).text = str(int(heart_rates[index]))
        if index < len(cadences):
            add_number(point, "Cadence", cadences[index])
        if index < len(speeds) or index < len(watts):
            extensions = ET.SubElement(point, q("Extensions"))
            tpx = ET.SubElement(extensions, f"{{{TCX_EXT_NS}}}TPX")
            if index < len(speeds):
                add_number(tpx, "Speed", speeds[index])
            if index < len(watts):
                add_number(tpx, "Watts", watts[index])

    creator = ET.SubElement(activity_el, q("Creator"), {f"{{{XSI_NS}}}type": "tcx:Device_t"})
    ET.SubElement(creator, q("Name")).text = creator_name
    ET.SubElement(creator, q("UnitId")).text = "0"
    ET.SubElement(creator, q("ProductID")).text = "0"
    version = ET.SubElement(creator, q("Version"))
    for field in ("VersionMajor", "VersionMinor", "BuildMajor", "BuildMinor"):
        ET.SubElement(version, q(field)).text = "0"
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def detail_paths(activity_id: Any) -> tuple[Path, Path]:
    folder = data_dir() / "activity_details"
    identifier = str(activity_id)
    return folder / f"{identifier}.json", folder / f"{identifier}.tcx"


def write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_full_records(
    activities: list[dict[str, Any]], access: str, *, refresh: bool = False
) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Fetch details and high-resolution streams, reusing completed local records."""
    references: dict[str, dict[str, Any]] = {}
    cached_count = 0
    failed_count = 0
    for summary in activities:
        activity_id = summary.get("id")
        if activity_id is None:
            failed_count += 1
            continue
        json_path, tcx_path = detail_paths(activity_id)
        rel_json = json_path.relative_to(data_dir()).as_posix()
        rel_tcx = tcx_path.relative_to(data_dir()).as_posix()
        record: dict[str, Any] = {}
        if json_path.exists():
            try:
                record = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {}
        is_complete = bool(record.get("streams_fetched") and record.get("activity"))
        if is_complete and not refresh:
            cached_count += 1
            record["summary"] = summary
            write_record(json_path, record)
            if not tcx_path.exists():
                build_tcx(record, tcx_path)
            references[str(activity_id)] = {
                "json": rel_json, "tcx": rel_tcx, "status": "complete",
            }
            continue

        record.setdefault("source", "Strava API v3")
        record["summary"] = summary
        record.setdefault("streams_fetched", False)
        try:
            if refresh or not record.get("activity"):
                record["activity"] = http_json(
                    f"{API_BASE}/activities/{activity_id}", access_token=access
                )
                record["laps"] = record["activity"].get("laps", [])
                if "laps" not in record["activity"]:
                    record["laps"] = http_json(
                        f"{API_BASE}/activities/{activity_id}/laps", access_token=access
                    )
                record["streams_fetched"] = False
                write_record(json_path, record)
            if refresh or not record.get("streams_fetched"):
                params = urllib.parse.urlencode({
                    "keys": ",".join(FULL_STREAM_KEYS),
                    "key_by_type": "true",
                    "resolution": "high",
                })
                response = http_json(
                    f"{API_BASE}/activities/{activity_id}/streams?{params}",
                    access_token=access,
                )
                if isinstance(response, list):
                    response = {item.get("type", str(i)): item for i, item in enumerate(response)}
                record["streams"] = response if isinstance(response, dict) else {}
                record["streams_fetched"] = True
            record.pop("fetch_error", None)
            write_record(json_path, record)
            build_tcx(record, tcx_path)
            references[str(activity_id)] = {
                "json": rel_json, "tcx": rel_tcx, "status": "complete",
            }
        except RuntimeError as exc:
            record["fetch_error"] = str(exc)
            write_record(json_path, record)
            status = "partial" if record.get("activity") else "unavailable"
            references[str(activity_id)] = {
                "json": rel_json if json_path.exists() else None,
                "tcx": rel_tcx if tcx_path.exists() else None,
                "status": status,
                "error": str(exc),
            }
            failed_count += 1
            if "rate limit" in str(exc).lower():
                break
    return references, cached_count, failed_count


def write_data(
    activities: list[dict[str, Any]], days: int,
    references: dict[str, dict[str, Any]],
    *, source: str = "Strava API v3 /athlete/activities, /activities/{id}, and /activities/{id}/streams",
    notes: str = "Full activity detail and high-resolution streams are stored in activity_details/{id}.json; TCX trackpoints are in activity_details/{id}.tcx.",
) -> tuple[Path, Path]:
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    json_path = folder / "activities_42days.json" if days == 42 else folder / f"activities_{days}days.json"
    document = {
        "source": source,
        "fetched_at_utc": fetched_at,
        "window_days": days,
        "activity_count": len(activities),
        "notes": notes,
        "activities": [
            {**activity, "local_files": references.get(str(activity.get("id")))}
            for activity in activities
        ],
    }
    csv_path = folder / "activities_42days.csv" if days == 42 else folder / f"activities_{days}days.csv"
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for activity in activities:
            writer.writerow({field: activity.get(field) for field in FIELDS})
    return json_path, csv_path


def import_fit_directory(
    input_dir: Path, days: int, *, if_present: bool = False
) -> tuple[Path | None, Path | None, int, int, int, list[str]]:
    """Import recent FIT activities and write the same index/detail layout as fetch."""
    from fit_import import parse_fit_activity

    if not input_dir.is_dir():
        if if_present:
            return None, None, 0, 0, 0, []
        raise RuntimeError(
            f"FIT input folder does not exist: {input_dir}. Put .fit files there or pass --input."
        )
    fit_files = sorted(
        (path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".fit"),
        key=lambda path: str(path).casefold(),
    )
    if not fit_files:
        if if_present:
            return None, None, 0, 0, 0, []
        raise RuntimeError(f"No .fit files found under {input_dir}.")

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    parse_errors: list[str] = []
    seen_ids: set[str] = set()
    expired_count = 0
    duplicate_count = 0

    for fit_path in fit_files:
        try:
            summary, record = parse_fit_activity(fit_path)
            activity_id = str(summary["id"])
            if activity_id in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(activity_id)
            if parse_datetime(str(summary["start_date"])) < cutoff:
                expired_count += 1
                continue
            entries.append((summary, record))
        except Exception as exc:
            parse_errors.append(f"{fit_path.name}: {exc}")

    if not entries and parse_errors and expired_count == 0 and duplicate_count == 0:
        detail = "\n".join(parse_errors[:5])
        raise RuntimeError(f"No recent FIT activities could be imported.\n{detail}")

    entries.sort(key=lambda entry: parse_datetime(str(entry[0]["start_date"])), reverse=True)
    activities = [summary for summary, _ in entries]
    records = [record for _, record in entries]

    references: dict[str, dict[str, Any]] = {}
    for summary, record in zip(activities, records):
        activity_id = str(summary["id"])
        json_path, tcx_path = detail_paths(activity_id)
        record["summary"] = summary
        write_record(json_path, record)
        build_tcx(record, tcx_path, creator_name="Local FIT import")
        references[activity_id] = {
            "json": json_path.relative_to(data_dir()).as_posix(),
            "tcx": tcx_path.relative_to(data_dir()).as_posix(),
            "status": "complete",
        }

    json_path, csv_path = write_data(
        activities,
        days,
        references,
        source="Local FIT file import",
        notes=(
            "Activities were parsed from local FIT files. Full activity detail, laps, and available time-series "
            "streams are stored in activity_details/{id}.json; normalized trackpoints are in activity_details/{id}.tcx."
        ),
    )
    return json_path, csv_path, len(fit_files), expired_count, duplicate_count, parse_errors


def prune_expired_records(cutoff: dt.datetime) -> int:
    """Remove activity details older than the fetched window, preserving unknown files."""
    folder = data_dir() / "activity_details"
    if not folder.exists():
        return 0
    removed = 0
    for json_path in folder.glob("*.json"):
        if not json_path.stem.isdigit():
            continue
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
            activity = record.get("activity") or record.get("summary") or {}
            start_date = activity.get("start_date")
            if not start_date or parse_datetime(start_date) >= cutoff:
                continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
            continue
        json_path.unlink()
        json_path.with_suffix(".tcx").unlink(missing_ok=True)
        removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Strava activities or import local FIT files for analysis."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth", help="Authorize this tool with Strava")
    fetch_parser = subparsers.add_parser("fetch", help="Fetch recent activities and full streams")
    fetch_parser.add_argument("--days", type=int, default=42, help="Lookback window (default: 42)")
    fetch_parser.add_argument(
        "--refresh-details", action="store_true",
        help="Re-download details and streams even when a local complete copy exists",
    )
    fit_parser = subparsers.add_parser(
        "import-fit", help="Import local FIT files into the same JSON/CSV/TCX layout"
    )
    fit_parser.add_argument(
        "--input", "--input-dir", dest="input_dir", type=Path,
        default=Path(__file__).resolve().parent / "fit_import",
        help="Folder containing .fit files (default: ./fit_import)",
    )
    fit_parser.add_argument("--days", type=int, default=42, help="Lookback window (default: 42)")
    fit_parser.add_argument(
        "--if-present", action="store_true",
        help="Leave existing data untouched when the input folder has no FIT files",
    )
    args = parser.parse_args()
    try:
        if args.command == "auth":
            authorize()
        elif args.command == "fetch":
            if args.days < 1 or args.days > 3650:
                raise RuntimeError("--days must be between 1 and 3650.")
            access = valid_access_token()
            cutoff = dt.datetime.fromtimestamp(
                int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)).timestamp()),
                tz=dt.timezone.utc,
            )
            activities = fetch_activities(int(cutoff.timestamp()), access)
            references, cached_count, failed_count = fetch_full_records(
                activities, access, refresh=args.refresh_details
            )
            json_path, csv_path = write_data(activities, args.days, references)
            removed_count = prune_expired_records(cutoff)
            completed = sum(ref.get("status") == "complete" for ref in references.values())
            print(f"Found {len(activities)} activities from the last {args.days} days.")
            print(f"Full records ready: {completed}; reused from local cache: {cached_count}; incomplete: {failed_count}.")
            print(f"Expired activity records removed: {removed_count}.")
            print(f"JSON: {json_path}")
            print(f"CSV:  {csv_path}")
            print(f"Per-activity JSON and TCX: {data_dir() / 'activity_details'}")
            if failed_count:
                print("Some records are incomplete. Check local_files.status in the index; rerun fetch to resume.")
        else:
            if args.days < 1 or args.days > 3650:
                raise RuntimeError("--days must be between 1 and 3650.")
            json_path, csv_path, file_count, expired_count, duplicate_count, parse_errors = import_fit_directory(
                args.input_dir, args.days, if_present=args.if_present
            )
            if json_path is None or csv_path is None:
                print(f"No FIT files found in {args.input_dir}; existing activity data was left unchanged.")
                return 0
            try:
                activity_count = json.loads(json_path.read_text(encoding="utf-8"))["activity_count"]
            except (OSError, ValueError, KeyError) as exc:
                raise RuntimeError(f"Could not read generated activity index: {exc}") from exc
            print(f"Scanned {file_count} FIT files; imported {activity_count} activities in the last {args.days} days.")
            print(f"Skipped outside-window activities: {expired_count}; duplicate files: {duplicate_count}; parse errors: {len(parse_errors)}.")
            print(f"JSON: {json_path}")
            print(f"CSV:  {csv_path}")
            print(f"Per-activity JSON and TCX: {data_dir() / 'activity_details'}")
            for error in parse_errors:
                print(f"Skipped invalid FIT: {error}", file=sys.stderr)
    except (RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
