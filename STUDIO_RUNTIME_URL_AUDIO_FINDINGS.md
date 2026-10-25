# Studio runtime and URL audio investigation — 2026-10-25

No deployment, push, production environment edit, or paid API request was performed.
Local branch: `fix/studio-runtime-url-audio-20261025`.

## Deployed observations

Read-only checks from the deployed frontend origin against
`https://clipper-api-final-1.onrender.com` returned:

| Route | Result |
| --- | --- |
| `/api/health` | 200, `ok: true` |
| `/studios-api/health` | 404 |
| `/studios-api/studio/capabilities` | 404 |

The backend is reachable, but that service does not expose the expected Studio
mount. Its current environment and deployed commit could not be verified because
no authenticated Render dashboard session is available. A disabled/misformatted
flag or a backend revision without the mount remains possible. OpenAPI does not
describe mounted child apps, so absence from its schema alone is not proof.

The existing source gated both bootstrap and installation on the literal string
`1`. This update also accepts `true`, `yes`, and `on`, case-insensitively and with
surrounding whitespace. Unset/false/unknown values remain disabled. No flag was
enabled on Render or in local environment files. The local-only access flag and
Studio authentication, ownership, and billing middleware remain unchanged.

The new public `/api/studios/status` endpoint reports enabled/mounted booleans,
FFmpeg/FFprobe availability, and installed downloader version only. After an
approved deployment, it distinguishes configuration, mount, and executable issues
without exposing credentials or filesystem paths.

## Verified source defects and local fixes

- URL downloads forced an MP4 format rather than selecting and merging the best
  video/audio streams. They now use yt-dlp `bv*+ba/b`, retaining actual output
  paths and using FFmpeg merging when necessary.
- A pre-created output file made an existence-only success check unreliable.
  Downloads now require nonempty, FFprobe-readable media with actual streams.
- Direct HTML/error responses could reach FFmpeg as purported MP4 files. They
  now produce an actionable media error; silent videos are identified explicitly.
- MP3 uploads could use the same filename for FFmpeg input and output. Conversion
  now uses a separate temporary path and removes the converted file afterward.
- The downloader pin was `2025.1.26`. It is now `2026.10.21`, verified against
  the official yt-dlp latest release and the installed local version.
- Downloader errors do not echo raw URLs, tokens, or stderr. No browser cookies,
  login bypass, new credentials, or external configuration are loaded.

The existing Dockerfile uses Python 3.11 on Debian Bookworm and installs FFmpeg.
It was not changed. A Docker build was not run during this investigation, and
FFmpeg availability on the currently deployed instance remains unverified.

The exact reported TikTok URL has not been supplied. Consequently its failure
has not been reproduced against TikTok or Render, and these local repairs are
not a claim that TikTok's server-side restrictions have been resolved.

## Tests actually run

- `python -m unittest tests.test_url_media tests.test_studios_config tests.test_studio_access -v`:
  26 passed. Actual FFmpeg encoding/probing and loopback HTTP downloads; actual
  transcription route body with mocked cloud transcription/database boundaries;
  HTML/empty/silent media rejection; MP3 input preservation; downloader-output
  contract and sanitized errors; flag parsing/mounting; original route preservation;
  authentication, ownership, billing, and usage regressions. No real cloud call.
- Existing local `actual-video-20s.mp4`: audio successfully extracted to a distinct
  MP3, probed duration 20.011 seconds. This checks conversion, not transcript quality.
- Three existing editor integration tests passed against the local loopback Studio
  server using that real video: trim/split/reorder, images/text/overlays/fades,
  original audio mute/volume, music/effects timing and separate volume controls.
  Those exercise unchanged editor code, not a deployed Render instance.
- `git diff --check`: passed.

The first added route-body test hit Windows console encoding on an existing emoji
log statement. The test harness now captures printing; that unrelated application
logging code was not changed. The final test run passed.

## Preservation and remaining verification

No frontend files were modified. Its five navigation entries remain Original
ClipForge, Auto Clip Studio, Transcript Clip Studio, Thumbnail Studio, and Video
Editor. Original Transcribe/Clip routes remain. Authentication, billing, database,
Supabase, Docker, Render configuration, credentials, and paid-generation switches
were not changed. Protected backend module equality is regression-tested.

Before any approved deployment, review the backend branch. Then verify the deployed
backend revision and `CLIPFORGE_ENABLE_STUDIOS=1` explicitly; do not set the local
access bypass on a public server. Existing local transcription/AI functionality
also requires the already-approved model files and appropriate server resources;
none were uploaded or downloaded here. Keep paid generation disabled. Check the
new status endpoint, authenticated Studio access, and the supplied TikTok link on
that instance. No full authenticated production rendering/transcription test has
been claimed.

Changed files: `app.py`, `url_media.py`, `clipforge_studios/flags.py`,
`clipforge_studios/install.py`, `requirements.txt`, `tests/test_url_media.py`,
`tests/test_studios_config.py`, and this report.
