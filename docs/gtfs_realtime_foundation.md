# GTFS-Realtime Foundation

## Purpose

The Version 2 foundation establishes configuration, secret handling,
validation, and local raw-storage conventions for STM GTFS-Realtime capture.

The one-shot capture increment can download and preserve an explicitly
selected feed. It does not parse protobuf messages or calculate delays,
punctuality, or reliability metrics.

## Configuration

Nonsecret configuration is stored in:

```text
config/gtfs_realtime.json
```

| Field | Purpose |
|---|---|
| `schema_version` | Version of the local configuration contract |
| `provider` | Provider identifier; currently `stm` |
| `timezone` | Human-facing project timezone; currently `America/Montreal` |
| `storage_root` | Relative local root for raw responses |
| `allowed_feed_types` | Supported feed categories |
| `api_key_environment_variable` | Name of the environment variable containing the secret |
| `authentication_header` | STM API-key request header; `apiKey` |
| `accept_header` | Requested payload media type; `application/x-protobuf` |
| `request_timeout_seconds` | Positive timeout for a capture request |
| `maximum_response_bytes` | Maximum accepted response size in bytes |
| `endpoints` | Confirmed HTTPS STM endpoints by feed type |

## Confirmed STM API contract

Both feeds use HTTP `GET`.

| Feed | Endpoint |
|---|---|
| Vehicle Positions | `https://api.stm.info/pub/od/gtfs-rt/ic/v2/vehiclePositions` |
| Trip Updates | `https://api.stm.info/pub/od/gtfs-rt/ic/v2/tripUpdates` |

Requests use:

```text
apiKey: <value from STM_GTFS_REALTIME_API_KEY>
Accept: application/x-protobuf
```

The API key is sent directly through the `apiKey` header. No Bearer prefix is
used.

An HTTP `200` response was observed through the STM developer portal Swagger
test. The successful response body is binary GTFS-Realtime Protocol Buffers.
This observation confirms the contract but does not make availability or
payload validity assumptions.

Configuration validation is local. The separate capture command sends a
request only when it is run without `--dry-run`.

## API key

The API key must be supplied through:

```text
STM_GTFS_REALTIME_API_KEY
```

For the current PowerShell session:

```powershell
$env:STM_GTFS_REALTIME_API_KEY = "YOUR_STM_API_KEY"
```

Validate the configuration and environment variable:

```powershell
python .\src\gtfs_realtime_config.py
```

Validate only nonsecret configuration:

```powershell
python .\src\gtfs_realtime_config.py --skip-credential-validation
```

The project does not automatically load `.env` files and does not require
`python-dotenv`. `.env.example` contains a placeholder only.

## Security rules

- Never store the API key in JSON configuration.
- Never commit a populated `.env` file.
- Never pass the API key as a command-line argument.
- Never print the key or include it in exceptions, object representations,
  metadata, documentation, reports, or URLs written to logs.
- Never copy or publish a Swagger-generated curl command containing the key.
- Missing and blank keys fail with an error that names only the required
  environment variable.
- Configuration loading and path derivation do not create files or
  directories.
- Endpoint URLs cannot contain credentials, query parameters, fragments, or
  whitespace.

## Local storage convention

Raw captures are separated from static GTFS:

```text
data/raw/gtfs_realtime/stm/
├── vehicle_positions/YYYY/MM/DD/
└── trip_updates/YYYY/MM/DD/
```

Payload and sidecar metadata names use:

```text
<UTC_TIMESTAMP>_<CAPTURE_UUID>.pb
<UTC_TIMESTAMP>_<CAPTURE_UUID>.json
```

Example:

```text
20260725T123456Z_12345678-1234-5678-1234-567812345678.pb
20260725T123456Z_12345678-1234-5678-1234-567812345678.json
```

`src/gtfs_realtime_config.py` derives and validates these paths. It rejects
unsupported feed types, absolute storage roots, traversal, and paths that
resolve outside the configured project root.

`MONTREAL_TRANSIT_PROJECT_ROOT` redirects configuration and derived storage
paths into an alternate workspace for isolated tests.

## One-shot capture

The next increment now provides a controlled one-shot capture command that
preserves one untouched binary response and a nonsecret metadata sidecar.

See [One-Shot GTFS-Realtime Capture](gtfs_realtime_capture.md) for CLI usage,
redirect rejection, streaming limits, atomic persistence, metadata, cleanup,
and manual validation.

## Current limitations

This increment does not:

- parse GTFS-Realtime protobuf messages;
- load real-time data into DuckDB;
- measure freshness, completeness, delay, punctuality, or reliability.

## Planned next step

The next increment can validate captured payload structure and add protobuf
parsing after raw response preservation. It must continue to tolerate
unavailable, incomplete, stale, empty, or invalid responses.

See [Data Source, Attribution, and Terms of Use](data_source_and_terms.md) for
STM attribution, licence information, availability limitations, and the
unofficial-project disclaimer.
