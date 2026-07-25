# GTFS-Realtime Foundation

## Purpose

The first Version 2 increment establishes configuration, secret handling,
validation, and local raw-storage conventions for future STM GTFS-Realtime
capture.

It does not make network requests, download feeds, write capture files, parse
protobuf messages, or calculate delays, punctuality, or reliability metrics.

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
| `storage_root` | Relative local root for future raw responses |
| `allowed_feed_types` | Supported feed categories |
| `api_key_environment_variable` | Name of the environment variable containing the secret |
| `authentication_header` | STM API-key request header; `apiKey` |
| `accept_header` | Requested payload media type; `application/x-protobuf` |
| `request_timeout_seconds` | Positive timeout reserved for controlled future capture |
| `maximum_response_bytes` | Positive response-size limit reserved for future capture |
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

The foundation validates these values locally. It does not send requests.

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

Future raw captures will be separated from static GTFS:

```text
data/raw/gtfs_realtime/stm/
├── vehicle_positions/YYYY/MM/DD/
└── trip_updates/YYYY/MM/DD/
```

Future payload and sidecar metadata names will use:

```text
<UTC_TIMESTAMP>_<CAPTURE_UUID>.pb
<UTC_TIMESTAMP>_<CAPTURE_UUID>.json
```

Example:

```text
20260725T123456123456Z_12345678-1234-5678-1234-567812345678.pb
20260725T123456123456Z_12345678-1234-5678-1234-567812345678.json
```

`src/gtfs_realtime_config.py` currently derives and validates these paths
only. It rejects unsupported feed types, absolute storage roots, traversal,
and paths that resolve outside the configured project root.

`MONTREAL_TRANSIT_PROJECT_ROOT` redirects configuration and derived storage
paths into an alternate workspace for isolated tests.

## Current limitations

This increment does not:

- perform HTTP requests;
- create capture directories;
- preserve response bytes;
- generate capture metadata;
- parse GTFS-Realtime protobuf messages;
- load real-time data into DuckDB;
- measure freshness, completeness, delay, punctuality, or reliability.

## Planned next step

The next increment can add controlled network capture with timeouts,
response-size limits, atomic raw writes, SHA-256 metadata, and secret-safe
errors. It must tolerate unavailable, incomplete, stale, empty, or invalid
responses. Raw response preservation should precede protobuf parsing.

See [Data Source, Attribution, and Terms of Use](data_source_and_terms.md) for
STM attribution, licence information, availability limitations, and the
unofficial-project disclaimer.
