# One-Shot GTFS-Realtime Capture

## Purpose

`src/capture_gtfs_realtime.py` captures exactly one selected STM
GTFS-Realtime feed response. It preserves the response bytes unchanged in a
`.pb` file and writes a nonsecret JSON metadata sidecar.

The command supports:

```text
vehicle_positions
trip_updates
```

It does not parse Protocol Buffers, load real-time data into DuckDB, schedule
recurring captures, or calculate operational metrics.

## API key

The API key is read only from:

```text
STM_GTFS_REALTIME_API_KEY
```

Set it interactively for the current PowerShell session:

```powershell
$secureKey = Read-Host "STM API key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:STM_GTFS_REALTIME_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
```

Remove it when finished:

```powershell
Remove-Item Env:STM_GTFS_REALTIME_API_KEY
```

Never paste or publish Swagger-generated curl commands containing
credentials.

## Dry run

A dry run validates configuration and credentials, resolves the endpoint,
generates a UTC timestamp and UUID, and previews nonsecret destination
information:

```powershell
python .\src\capture_gtfs_realtime.py --feed vehicle_positions --dry-run
python .\src\capture_gtfs_realtime.py --feed trip_updates --dry-run
```

It makes no request and creates no directory or file.

## One real capture

After reviewing a dry run:

```powershell
python .\src\capture_gtfs_realtime.py --feed vehicle_positions
python .\src\capture_gtfs_realtime.py --feed trip_updates
```

Each invocation makes one HTTPS `GET` request using:

```text
Accept: application/x-protobuf
apiKey: value from STM_GTFS_REALTIME_API_KEY
```

No Bearer prefix is used.

## Redirect policy

All redirects are rejected, including HTTP 301, 302, 303, 307, and 308. The
capture command never resends the `apiKey` header to a redirected destination.

## Response validation and streaming

Only HTTP 200 is accepted. The response must have a nonempty
`application/x-protobuf` body. Media-type comparison is case-insensitive and
ignores optional parameters.

The payload is read in bounded 64 KiB chunks. SHA-256 and byte count are
calculated incrementally while the untouched bytes are written. If
`Content-Length` exceeds `maximum_response_bytes`, capture stops before
reading the body. The same maximum is enforced while streaming when the
header is absent or incorrect.

When `Content-Length` is present, it must be a nonnegative decimal integer,
must be greater than zero, and must exactly equal the stored byte count.
Malformed, conflicting, truncated, or unexpectedly longer responses reject
the capture without creating final files. A missing `Content-Length` is
allowed when the streamed body is nonempty and passes the size limit.
Incomplete responses and other HTTP protocol failures also reject the
capture.

## Storage layout

```text
data/raw/gtfs_realtime/stm/
├── vehicle_positions/YYYY/MM/DD/
└── trip_updates/YYYY/MM/DD/
```

Payload and metadata share one UTC timestamp and UUID:

```text
YYYYMMDDTHHMMSSZ_<CAPTURE_UUID>.pb
YYYYMMDDTHHMMSSZ_<CAPTURE_UUID>.json
```

All raw real-time files remain excluded from Git through `data/raw/`.

## Atomic persistence and cleanup

Temporary payload and metadata files are created inside the final destination
directory with a `.part-<UUID>` suffix. Both files are flushed and synchronized
before finalization.

Finalization uses an atomic same-directory hard link and refuses to overwrite
an existing path. If metadata finalization fails after payload finalization,
the payload is rolled back. Temporary files are removed after every handled
failure.

The payload and metadata therefore form a rollback-based logical pair. This
is not a cross-file transactional filesystem operation: the payload may be
briefly visible before metadata finalization completes.

The payload and metadata are treated as one logical capture pair.

## Metadata schema

Metadata uses schema version 1 and deterministic UTF-8 JSON with sorted keys,
readable indentation, and a trailing newline.

| Field | Meaning |
|---|---|
| `schema_version` | Metadata contract version |
| `provider` | `stm` |
| `feed_type` | Captured feed |
| `endpoint` | Official nonsecret endpoint |
| `http_method` | `GET` |
| `http_status` | `200` |
| `response_content_type` | Normalized protobuf media type |
| `response_size_bytes` | Bytes stored |
| `sha256` | SHA-256 of stored bytes |
| `capture_uuid` | Capture identifier |
| `captured_at_utc` | ISO UTC capture time |
| `filename_timestamp_utc` | Filesystem-safe UTC timestamp |
| `payload_relative_path` | Project-relative POSIX path |
| `metadata_relative_path` | Project-relative POSIX path |
| `request_timeout_seconds` | Configured request timeout |
| `maximum_response_bytes` | Configured maximum payload size |
| `response_content_length_header` | Optional validated header value |

Metadata never includes API keys, request headers, cookies, credentials,
environment dumps, absolute local paths, or complete response headers.

## Failure behavior

The command returns a nonzero exit code for configuration, credential, HTTP,
redirect, connection, timeout, content-type, empty-body, size, and persistence
failures. User-facing errors are concise and do not include request objects or
credentials.

Future automation must tolerate unavailable, incomplete, stale, empty, or
invalid STM responses.

## Manual validation

Run the complete network-free suite first:

```powershell
python -m compileall -q src tests
python -m unittest discover -s tests -p "test_*.py" -v
```

Then set the API key interactively, run both dry runs, and perform only the
single feed capture you intend to review.

## Current limitations

- No protobuf parsing.
- No DuckDB real-time tables.
- No feed freshness or completeness controls.
- No delays, punctuality, or reliability indicators.
- No recurring scheduler or retention policy.

See [Data Source, Attribution, and Terms of Use](data_source_and_terms.md).
