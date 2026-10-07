---
name: strava-analysis
description: Import local FIT activities and analyze Strava-shaped JSON, CSV, or TCX data for intervals, power, heart rate, cadence, and training trends.
---

# Strava Training Analysis

Use this skill to analyze Strava activity data available in the user's workspace or supplied for the task. Do not fetch or refresh account data unless the user asks.

When this repository is available, run `python strava_tool.py import-fit --if-present --days 42` before reading the activity index, unless the user specifically chose the Strava API dataset or another day window. The program scans `fit_import/`, filters by activity start time, and creates the analysis files; do not ask the model to decode FIT data. If the user supplied a different folder, pass it with `--input`. If no FIT files are present, the command leaves the existing API dataset untouched. Pass the requested day window through `--days` when it differs from 42.

## Find and read the data

- If the user names a file or date window, start there. Otherwise look for `strava_data/activities_42days.json` or a matching `activities_<days>days.json` index after running the FIT import step above.
- Read the index first. Each item may have `local_files.json` and `local_files.tcx` paths; resolve them relative to the index/project and read per-activity JSON for detail, laps, and streams.
- Per-activity JSON is the source for available Strava API or FIT streams. TCX can help inspect exported trackpoints, but do not claim it contains data absent from the imported file or API response.
- For large stream files, compute from only the needed fields locally instead of printing or loading full GPS and sensor arrays into the conversation.
- If only a CSV or TCX is available, explain which fields or high-resolution streams are unavailable for the requested analysis.

## Analyze and report

- For intervals, short efforts, and best-effort power such as CP5, use the raw `time` and `watts` streams when present. Align samples by timestamps and account for sample spacing when calculating averages; do not substitute whole-activity `average_watts` or an unweighted mean when the sampling is uneven. State the window duration and the method if the result is approximate.
- Use `heartrate` and `cadence` streams to describe response and pedaling when present. Use laps as recorded boundaries where they fit the question; do not assume every lap is a work interval.
- Treat missing or empty fields and streams as unavailable, never as zero. Distinguish elapsed time from moving time and activity summaries from stream-derived calculations.
- State units when reporting: distance and elevation in meters, time in seconds, speed in meters per second, heart rate in bpm, power in watts, cadence in rpm, and stream time in seconds from activity start. State the timezone used. Prefer `start_date_local` with the activity's timezone for calendar grouping; use UTC only when the question calls for it or local timezone is unavailable.
- Be clear that Strava API exports are not original device FIT files and cannot recover sensor data Strava did not retain or expose. Training metrics are descriptive and do not establish a medical diagnosis.

## Protect private activity data

Activity names, dates, IDs, GPS tracks, start/end locations, and physiological streams can identify or expose the user. Keep summaries to the details needed for the request; do not reveal exact routes, home locations, credentials, or unnecessary identifiers. Do not publish, upload, or send activity files or credentials to another service unless the user explicitly asks.

## Refresh data

Only refresh when asked. If this repository's `strava_tool.py` is available, the user can run `python strava_tool.py fetch` (or an explicitly requested day window); it requires the user's own Strava API credentials and OAuth authorization. OAuth tokens are cached outside the repository. Never include Client Secrets, access tokens, or refresh tokens in analysis output.
