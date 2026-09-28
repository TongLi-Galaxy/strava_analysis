# Strava activity data

- Start analysis with strava_data/activities_42days.json or the matching day-window file. Each activity's local_files.json points to its full per-activity record; local_files.tcx points to its TCX export.
- Per-activity JSON contains Strava activity detail, laps, and high-resolution streams. Use raw watts and time streams for short-interval or CP5 analysis instead of relying on whole-activity averages. TCX preserves available trackpoint data; JSON is the source for every returned stream.
- Explain units when reporting: distance/elevation in meters, time in seconds, speed in meters per second, heart rate in bpm, power in watts, and cadence in rpm. Stream time is seconds from activity start. Prefer local start time for calendar grouping and state which timezone is used.
- Treat missing or empty API fields/streams as unavailable; do not infer zero. Strava API data is not the original device FIT file and cannot include sensor data Strava did not retain or expose.
- Refresh current data with python strava_tool.py fetch when requested. OAuth tokens are cached outside this workspace. Do not include the configured API Client Secret or OAuth tokens in analysis output.
