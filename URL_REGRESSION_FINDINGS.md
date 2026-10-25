# Original ClipForge URL investigation — 2026-10-25

Local fixes only. No push, merge, deployment, production source/environment edit,
paid API call, credential/price change, or subscription change was performed.
Local backend/frontend branches: `fix/url-download-regressions-20261025`.

## Revisions and deployed runtime

Render's authenticated dashboard confirms the backend runs `8ad3cf7` from main
(the billing-fix merge). GitHub frontend main is `d17c9a8`. URL downloading was
changed by `a2270fe`, merged as `0726a78`; the subsequent billing update did not
change the downloader. The last pre-Studio production revision visible in Render
is `6578a43`. Its URL helper and the immediate pre-downloader-update helper at
`9fbe4d3` were compared with current main. Historical success for the user's exact
URLs cannot be established without those URLs and failure times.

The live backend status endpoint returns:

- FFmpeg available: true; FFprobe available: true.
- yt-dlp: `2026.10.21`, also confirmed in Render's shell.
- Studio switch enabled/mounted: false/false. This was not changed.

The runtime is Docker, Python 3.11, Debian Bookworm. No base-image/package install
failure was observed in the deployed runtime. Dockerfile and requirements remain
unchanged by this fix.

## Confirmed URL compatibility regression

`a2270fe` introduced rejection of every HTTP `text/*`, JSON or XML content type
before reading/inspecting the media. Some valid video endpoints serve MP4 bytes
with `text/plain` or another incorrect type.

Using real encoded MP4 bytes on a loopback HTTP server:

1. Original `6578a43` helper accepted the MP4 served as `text/plain` and FFprobe
   read its streams.
2. Current-main helper rejected the same MP4 as a webpage. The new regression
   test failed before the patch.
3. Patched helper accepts it after validating the actual bytes with FFprobe.
   HTML, empty, corrupt, and silent-media handling remains protected.

This is a proven introduced regression. It is not yet proven to be the cause of
the user's particular direct URL failure because that URL has not been supplied.

Normal HTTP redirects to an MP4 already worked and remain tested. A shared link
redirecting to a recognized platform HTML page now hands the resolved URL to the
extractor rather than treating its page as media. Redirect loops and read/socket
timeouts return sanitized errors. Extractor requests have bounded retries and
socket deadlines within the existing 900-second overall limit. Download and audio
conversion work now runs off the async request loop, keeping other requests
responsive while waiting for a host. This is not a claim that a synchronous HTTP
request can outlast every hosting/proxy deadline.

Failures log only a controlled reason/exception category, transport type, and
elapsed time; raw provider stderr, signed URLs, tokens and cookies are not logged.

## Direct MP4 tests, separate from TikTok

Public sources:

| Source | Local result | Deployed Render helper result |
| --- | --- | --- |
| MDN `flower.mp4` | Download and MP3 conversion passed | Download and MP3 conversion passed |
| Samplelib `sample-5s.mp4` | Download and MP3 conversion passed | Not run |
| Google `ForBiggerBlazes.mp4` | Source returned HTTP 403 | Not run |

Render's installed current helper downloaded MDN's real 1,128,375-byte MP4 with
video and audio streams and produced a 122,834-byte MP3. Temporary media was removed.
The shell test imported only `url_media`; it did not call transcription/OpenAI,
Stripe, Supabase or any paid generation provider.

Thus direct MP4 processing is not universally broken on Render, and FFmpeg is not
the cause of every URL failure. A source-side 403 is not a successful media test.

Public URLs tested:
- https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4
- https://download.samplelib.com/mp4/sample-5s.mp4
- https://storage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4

## TikTok: still unresolved, not declared restored

Used a public URL from yt-dlp's official extractor tests:
`https://www.tiktok.com/@leenabhushan/video/6748451240264420610`.

- Local and Render extraction failed before FFmpeg with an unexpected webpage
  response and a warning that browser impersonation support is unavailable.
- A documented curl-cffi dependency was installed into an ignored, isolated LOCAL
  test directory only. Retesting locally changed the diagnostic to: “Your IP
  address is blocked from accessing this post.”
- The official short-link test `https://vm.tiktok.com/ZTR45GpSF/` also failed
  locally with an unsupported resolved URL. That historical fixture may no longer
  be usable; this is not evidence about the user's short link.
- No dependency, cookie, proxy, credential, or host/network setting was installed
  or changed on Render. Adding curl-cffi to production was not presented as a
  verified fix: it did not restore the local public test.
- Read-only existence checks found no yt-dlp config in the usual `/etc`, root-user
  or `/app` locations checked, and no `/tmp/cookies.txt`. No contents/secrets were
  read. There is no evidence that `--ignore-config` dropped an existing config at
  those locations.

The deployed diagnostic establishes an extractor failure before video download;
it does not prove Render's egress IP is blocked, because the explicit IP-block
message was observed locally with the optional dependency. TikTok's current
restrictions must not be confused with the verified MP4 content-type regression.
No bot-check bypass, paid proxy, browser cookie import, or paid API was enabled.
The user's exact failing TikTok and direct MP4 URLs/times remain requested.

## Billing investigation preserved; another verified regression fixed

Render logs at approximately 18:55 on October 25 show repeated 500 responses from
`/billing/check-access`, failing inside `request.form()`:
`MultipartParseError: Did not find CR at end of boundary (40)`.

The new frontend sent an empty browser multipart FormData when there was no
Checkout reference. Production pins python-multipart 0.0.9, which rejects that
valid empty closing-boundary form. The newer local parser did not reproduce the
problem, explaining why the earlier tests missed it.

An isolated 0.0.9 installation reproduced the parser exception. The patched
backend recognizes exactly the empty multipart form without invoking that parser;
the same isolated-version test then passed. Malformed/oversized requests return
controlled 400/413 responses. The frontend now uses URLSearchParams instead of
empty multipart forms. Identity and payment verification still run; no paid access
is granted by this transport compatibility fix.

This is a confirmed billing-status failure, not proof of the user's payment or
the root cause of every video URL failure. Existing subscription reconciliation,
webhook signature checks, product/invoice/account validation, duplicate checkout
protection and all earlier billing fixes remain in place. Production payment
records have not been verified or changed in this task.

## Final local regression results

- 73 backend tests passed: URL media/transport, actual MP4 content-type regression,
  redirects, download deadlines and event-loop responsiveness, billing transport,
  verified payment contracts, auth/ownership/usage and Studio configuration.
- 11 frontend billing/Studio-settings tests passed; Vite build succeeded with
  dummy loopback configuration. No environment files were changed.
- The existing actual uploaded 20-second video generated five real Original
  ClipForge preview clips through the unchanged `build_clip` function. All five
  had readable audio/video streams and thumbnails. No cloud API was called.
- Original 1–5 clipping route/count contracts and upload audio-conversion tests
  also passed. Cloud transcription is mocked in route tests, not claimed as a
  real paid transcription run.
- The application request path was not tested end to end with a real production
  paid account. The Render direct-MP4 diagnostic exercised the installed media
  helper and FFmpeg, independently of authentication/billing/cloud transcription.
- No Docker rebuild or production deployment was performed during these tests.

Changed backend files: `app.py`, `url_media.py`, `billing_identity.py`,
`tests/test_url_media.py`, `tests/test_url_regressions.py`,
`tests/test_verified_billing.py`, and this report.
Changed frontend files: `src/billingClient.mjs`, `tests/billing.test.mjs`.

The five sections, navigation, original Transcribe/Clip UI, transcription provider,
Stripe prices/credentials, Supabase credentials, existing payment-verification
logic, and production deployment configuration were preserved. Do not call TikTok
restored or deploy based on the successful direct-MP4 tests alone.
