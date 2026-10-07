"""Convert local FIT activities into the Strava-analysis JSON schema."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from pathlib import Path
from typing import Any

UTC = dt.timezone.utc

SPORT_TYPES = {
    "cycling": ("Ride", "Ride"),
    "running": ("Run", "Run"),
    "walking": ("Walk", "Walk"),
    "hiking": ("Hike", "Hike"),
    "swimming": ("Swim", "Swim"),
    "rowing": ("Row", "Row"),
    "cross_country_skiing": ("NordicSki", "NordicSki"),
    "alpine_skiing": ("AlpineSki", "AlpineSki"),
    "snowboarding": ("Snowboard", "Snowboard"),
    "stand_up_paddleboarding": ("StandUpPaddling", "StandUpPaddling"),
    "kayaking": ("Canoeing", "Canoeing"),
    "canoeing": ("Canoeing", "Canoeing"),
    "fitness_equipment": ("Workout", "Workout"),
    "elliptical": ("Workout", "Elliptical"),
    "strength_training": ("WeightTraining", "WeightTraining"),
}

CYCLING_SUBSPORTS = {
    "mountain": "MountainBikeRide",
    "mountain_biking": "MountainBikeRide",
    "gravel_cycling": "GravelRide",
    "indoor_cycling": "VirtualRide",
    "virtual_activity": "VirtualRide",
    "e_bike_fitness": "EBikeRide",
    "e_bike_mountain": "EMountainBikeRide",
    "bmx": "Ride",
    "road": "Ride",
}


def _utc_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _first_value(message: Any, *field_names: str) -> Any:
    if message is None:
        return None
    for field_name in field_names:
        value = message.get_value(field_name)
        if value is not None:
            return value
    return None


def _first_number(message: Any, *field_names: str) -> int | float | None:
    for field_name in field_names:
        value = _finite_number(_first_value(message, field_name))
        if value is not None:
            return value
    return None


def _label(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _activity_type(sport: Any, sub_sport: Any) -> tuple[str, str, bool | None]:
    sport_name = _label(sport)
    sub_sport_name = _label(sub_sport)
    type_name, sport_type = SPORT_TYPES.get(
        sport_name,
        (sport_name.replace("_", " ").title() or "Workout", sport_name.replace("_", " ").title() or "Workout"),
    )
    trainer: bool | None = None
    if sport_name in {"cycling", "virtual_cycling"}:
        type_name = "Ride"
        sport_type = CYCLING_SUBSPORTS.get(sub_sport_name, "Ride")
        if sub_sport_name in {"indoor_cycling", "virtual_activity"}:
            trainer = True
    return type_name, sport_type, trainer


def _activity_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"fit-{digest.hexdigest()[:20]}"


def _local_offset_seconds(messages: list[Any]) -> int | None:
    for message in reversed(messages):
        utc_stamp = _utc_datetime(_first_value(message, "timestamp"))
        local_stamp = _utc_datetime(_first_value(message, "local_timestamp"))
        if utc_stamp is None or local_stamp is None:
            continue
        offset = int((local_stamp - utc_stamp).total_seconds())
        if -14 * 3600 <= offset <= 14 * 3600:
            return offset
    return None


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_gmt_offset(seconds: int) -> str:
    sign = "+" if seconds >= 0 else "-"
    minutes = abs(seconds) // 60
    return f"(GMT{sign}{minutes // 60:02d}:{minutes % 60:02d}) FIT local time"


def _weighted_average(
    samples: list[tuple[dt.datetime, int | float | None]],
) -> float | None:
    weighted_sum = 0.0
    duration_sum = 0.0
    for (stamp, value), (next_stamp, _) in zip(samples, samples[1:]):
        duration = (next_stamp - stamp).total_seconds()
        if value is None or duration <= 0:
            continue
        weighted_sum += float(value) * duration
        duration_sum += duration
    if duration_sum:
        return weighted_sum / duration_sum
    values = [float(value) for _, value in samples if value is not None]
    return values[0] if len(values) == 1 else None


def _stream(values: list[Any]) -> dict[str, Any]:
    return {
        "data": values,
        "series_type": "time",
        "original_size": len(values),
        "resolution": "high",
    }


def _make_laps(
    lap_messages: list[Any],
    activity_id: str,
    local_offset: int,
) -> list[dict[str, Any]]:
    laps: list[dict[str, Any]] = []
    for index, message in enumerate(lap_messages, start=1):
        lap_start = _utc_datetime(_first_value(message, "start_time"))
        elapsed = _first_number(message, "total_elapsed_time")
        moving = _first_number(message, "total_timer_time")
        if moving is None:
            moving = elapsed
        lap: dict[str, Any] = {
            "id": f"{activity_id}-lap-{index}",
            "lap_index": index,
            "start_date": _format_utc(lap_start) if lap_start else None,
            "start_date_local": (
                (lap_start + dt.timedelta(seconds=local_offset)).isoformat(timespec="seconds")
                if lap_start else None
            ),
            "elapsed_time": elapsed,
            "moving_time": moving,
            "distance": _first_number(message, "total_distance"),
            "total_elevation_gain": _first_number(message, "total_ascent"),
            "average_speed": _first_number(message, "avg_speed"),
            "max_speed": _first_number(message, "max_speed"),
            "average_heartrate": _first_number(message, "avg_heart_rate"),
            "max_heartrate": _first_number(message, "max_heart_rate"),
            "average_watts": _first_number(message, "avg_power"),
            "max_watts": _first_number(message, "max_power"),
            "average_cadence": _first_number(message, "avg_cadence"),
            "max_cadence": _first_number(message, "max_cadence"),
            "calories": _first_number(message, "total_calories"),
        }
        laps.append(lap)
    return laps


def parse_fit_activity(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a Strava-shaped summary and detail record for a single FIT file."""
    try:
        from fitparse import FitFile
    except ImportError as exc:
        raise RuntimeError(
            "FIT import needs fitparse. Install dependencies with: python -m pip install -r requirements.txt"
        ) from exc

    session_messages: list[Any] = []
    lap_messages: list[Any] = []
    activity_messages: list[Any] = []
    record_rows: list[dict[str, Any]] = []

    try:
        with FitFile(str(path), check_crc=True) as fit_file:
            for message in fit_file.get_messages():
                if message.name == "session":
                    session_messages.append(message)
                elif message.name == "lap":
                    lap_messages.append(message)
                elif message.name == "activity":
                    activity_messages.append(message)
                elif message.name == "record":
                    record_rows.append({
                        "timestamp": _utc_datetime(_first_value(message, "timestamp")),
                        "distance": _first_number(message, "distance"),
                        "lat": _first_number(message, "position_lat"),
                        "lon": _first_number(message, "position_long"),
                        "altitude": _first_number(message, "enhanced_altitude", "altitude"),
                        "speed": _first_number(message, "enhanced_speed", "speed"),
                        "heartrate": _first_number(message, "heart_rate"),
                        "cadence": _first_number(message, "cadence"),
                        "watts": _first_number(message, "power"),
                        "temp": _first_number(message, "temperature"),
                    })
    except Exception as exc:
        raise RuntimeError(f"Cannot parse {path.name}: {exc}") from exc

    record_rows = [row for row in record_rows if row["timestamp"] is not None]
    record_rows.sort(key=lambda row: row["timestamp"])
    session_messages.sort(
        key=lambda message: _utc_datetime(_first_value(message, "start_time")) or dt.datetime.max.replace(tzinfo=UTC)
    )
    activity_id = _activity_id(path)

    start_utc = next(
        (
            _utc_datetime(_first_value(message, "start_time"))
            for message in session_messages
            if _utc_datetime(_first_value(message, "start_time")) is not None
        ),
        None,
    )
    if start_utc is None and record_rows:
        start_utc = record_rows[0]["timestamp"]
    if start_utc is None:
        start_utc = next(
            (
                _utc_datetime(_first_value(message, "timestamp"))
                for message in activity_messages
                if _utc_datetime(_first_value(message, "timestamp")) is not None
            ),
            None,
        )
    if start_utc is None:
        raise RuntimeError(f"{path.name} has no usable activity start timestamp.")

    local_offset = _local_offset_seconds([*session_messages, *activity_messages])
    offset_seconds = local_offset if local_offset is not None else 0
    local_start = start_utc + dt.timedelta(seconds=offset_seconds)
    timezone_label = _format_gmt_offset(offset_seconds) if local_offset is not None else "UTC"

    session = session_messages[0] if session_messages else None
    record_start = start_utc
    stream_rows = [row for row in record_rows if row["timestamp"] >= record_start]
    stream_times: list[int | float] = []
    stream_data: dict[str, list[Any]] = {
        "distance": [],
        "latlng": [],
        "altitude": [],
        "velocity_smooth": [],
        "heartrate": [],
        "cadence": [],
        "watts": [],
        "temp": [],
    }
    weighted_samples: dict[str, list[tuple[dt.datetime, int | float | None]]] = {
        key: [] for key in ("velocity_smooth", "heartrate", "watts", "cadence")
    }

    for row in stream_rows:
        stamp = row["timestamp"]
        seconds = (stamp - start_utc).total_seconds()
        stream_times.append(int(seconds) if seconds.is_integer() else round(seconds, 3))
        stream_data["distance"].append(row["distance"])
        stream_data["latlng"].append(
            [row["lat"], row["lon"]] if row["lat"] is not None and row["lon"] is not None else None
        )
        stream_data["altitude"].append(row["altitude"])
        stream_data["velocity_smooth"].append(row["speed"])
        stream_data["heartrate"].append(row["heartrate"])
        stream_data["cadence"].append(row["cadence"])
        stream_data["watts"].append(row["watts"])
        stream_data["temp"].append(row["temp"])
        for summary_key, row_key in (
            ("velocity_smooth", "speed"),
            ("heartrate", "heartrate"),
            ("watts", "watts"),
            ("cadence", "cadence"),
        ):
            weighted_samples[summary_key].append((stamp, row[row_key]))

    streams: dict[str, Any] = {}
    if stream_times:
        streams["time"] = _stream(stream_times)
        for key, values in stream_data.items():
            if any(value is not None for value in values):
                streams[key] = _stream(values)

    start_date = _format_utc(start_utc)
    start_date_local = local_start.isoformat(timespec="seconds")
    sport = _first_value(session, "sport")
    sub_sport = _first_value(session, "sub_sport")
    if sport is None and lap_messages:
        sport = _first_value(lap_messages[0], "sport")
    if sub_sport is None and lap_messages:
        sub_sport = _first_value(lap_messages[0], "sub_sport")
    activity_type, sport_type, trainer = _activity_type(sport, sub_sport)
    name = _first_value(session, "name")
    if not isinstance(name, str) or not name.strip():
        name = f"{sport_type} {local_start:%Y-%m-%d %H:%M}"

    last_distance = next(
        (row["distance"] for row in reversed(stream_rows) if row["distance"] is not None),
        None,
    )
    record_duration = (
        (stream_rows[-1]["timestamp"] - start_utc).total_seconds()
        if stream_rows else None
    )
    elapsed_time = _first_number(session, "total_elapsed_time")
    if elapsed_time is None:
        elapsed_time = record_duration
    moving_time = _first_number(session, "total_timer_time")
    if moving_time is None:
        moving_time = elapsed_time
    distance = _first_number(session, "total_distance")
    if distance is None:
        distance = last_distance
    average_watts = _first_number(session, "avg_power")
    if average_watts is None:
        average_watts = _weighted_average(weighted_samples["watts"])
    average_heartrate = _first_number(session, "avg_heart_rate")
    if average_heartrate is None:
        average_heartrate = _weighted_average(weighted_samples["heartrate"])
    average_speed = _first_number(session, "avg_speed")
    if average_speed is None and distance is not None and moving_time:
        average_speed = distance / moving_time
    max_speed = _first_number(session, "max_speed")
    if max_speed is None:
        speed_values = [row["speed"] for row in stream_rows if row["speed"] is not None]
        max_speed = max(speed_values) if speed_values else None
    max_heartrate = _first_number(session, "max_heart_rate")
    if max_heartrate is None:
        hr_values = [row["heartrate"] for row in stream_rows if row["heartrate"] is not None]
        max_heartrate = max(hr_values) if hr_values else None
    max_watts = _first_number(session, "max_power")
    if max_watts is None:
        watt_values = [row["watts"] for row in stream_rows if row["watts"] is not None]
        max_watts = max(watt_values) if watt_values else None
    average_cadence = _first_number(session, "avg_cadence")
    if average_cadence is None:
        average_cadence = _weighted_average(weighted_samples["cadence"])
    kilojoules = _first_number(session, "total_work")
    if kilojoules is not None:
        kilojoules /= 1000
    elif average_watts is not None and moving_time is not None:
        kilojoules = average_watts * moving_time / 1000

    laps = _make_laps(lap_messages, activity_id, offset_seconds)
    has_heartrate = "heartrate" in streams
    device_watts = "watts" in streams
    summary: dict[str, Any] = {
        "id": activity_id,
        "id_str": activity_id,
        "name": name,
        "type": activity_type,
        "sport_type": sport_type,
        "start_date": start_date,
        "start_date_local": start_date_local,
        "timezone": timezone_label,
        "utc_offset": offset_seconds,
        "distance": distance,
        "moving_time": moving_time,
        "elapsed_time": elapsed_time,
        "total_elevation_gain": _first_number(session, "total_ascent"),
        "elev_high": _first_number(session, "enhanced_max_altitude", "max_altitude"),
        "elev_low": _first_number(session, "enhanced_min_altitude", "min_altitude"),
        "average_speed": average_speed,
        "max_speed": max_speed,
        "average_heartrate": average_heartrate,
        "max_heartrate": max_heartrate,
        "average_watts": average_watts,
        "max_watts": max_watts,
        "average_cadence": average_cadence,
        "start_latlng": next(
            (
                [row["lat"], row["lon"]]
                for row in stream_rows
                if row["lat"] is not None and row["lon"] is not None
            ),
            None,
        ),
        "end_latlng": next(
            (
                [row["lat"], row["lon"]]
                for row in reversed(stream_rows)
                if row["lat"] is not None and row["lon"] is not None
            ),
            None,
        ),
        "kilojoules": kilojoules,
        "device_watts": device_watts,
        "trainer": trainer,
        "has_heartrate": has_heartrate,
        "lap_count": len(laps),
    }
    activity = {**summary, "laps": laps}
    detail = {
        "source": "Local FIT file import",
        "streams_fetched": True,
        "summary": summary,
        "activity": activity,
        "laps": laps,
        "streams": streams,
    }
    return summary, detail
