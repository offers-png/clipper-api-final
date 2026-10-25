# TikTok and verification follow-up — 2026-10-25

No push, deployment, merge, production dependency install, secret/price edit,
cookie import, paid API call, subscription mutation, or production record repair
was performed. Local branches: `fix/tiktok-billing-diagnostics-20261025`.

## Actual failed Render requests

The authenticated Render log viewer shows the following on instance `pv2rw`:

| Time, October 25 | Observed result |
| --- | --- |
| 19:17:59 | Platform download failed after 0.78s; POST `/transcribe` returned 422 |
| 19:18:17 | Platform download failed after 1.51s; POST `/clip_multi` returned 500 |

Both contain the user's reported generic download error. They failed during
downloading, before FFmpeg/transcription. Their durations rule out hitting the
configured network/overall timeout. Raw extractor stderr and request URLs were
not retained by this deployed helper, so the exact failing TikTok URL cannot be
reconstructed from these entries. The URL and account email have been requested.

Backend main is `9e89402`, merging the prior URL/empty-form fixes. The running
module was checked in Render's shell and contains the new socket timeout. The
installed yt-dlp version remains `2026.10.21`; FFmpeg/FFprobe are available.

## TikTok root-cause evidence and limits

Compared with pre-Studio revision `6578a43` and the last URL change:

- Original downloader called `yt-dlp -f mp4`. Its comment mentioned cookies, but
  the command never passed a cookie file. Current downloading uses `bv*+ba/b`,
  FFmpeg merge, actual output-path validation, and bounded retries/timeouts.
- This failure occurs at webpage extraction, before format selection, merging,
  audio conversion or rendering. Reverting the format selector or increasing
  timeout is not supported by the observed failure.
- `/tmp/cookies.txt` is absent on the running instance. No cookie content was read
  and no cookies were imported. Earlier checks also found no yt-dlp configuration
  in the standard locations checked; no evidence supports claiming that dropping
  an existing config is the cause.
- A read-only Render-shell extraction of the public official yt-dlp test URL
  `https://www.tiktok.com/@leenabhushan/video/6748451240264420610` fails with an
  unexpected webpage response and a warning that no impersonation target exists.
- Direct module inspection confirms `curl_cffi` is absent on Render. This is a
  genuine missing prerequisite in the current image.

Local fix adds the documented, pinned `curl-cffi==0.16.3` dependency. Installing it
locally makes yt-dlp list supported Chrome/Safari/Firefox targets. The normal
extractor requests browser support automatically; no cookies, keys, proxy,
custom device credentials, or paid download service is introduced.

However, the actual local download with that dependency still fails with an
explicit IP-block response. The deployed server's exact restriction is not proved
to be the same: its current failure occurs without the dependency installed.
Therefore the dependency fix is a prerequisite repair, NOT a verified restoration
of TikTok downloads. No successful TikTok download is claimed. Production TikTok
acceptance testing still fails/is incomplete; do not deploy this as a proven
TikTok repair. The user's exact URL remains needed.

Redirect-to-platform-page, direct-MP4 redirects, redirect loops, silent/corrupt
media rejection, actual MP4 byte validation and timeout tests remain passing.
Expired/private/login-only links cannot be made public by changing FFmpeg.

## Separate direct-MP4 test on the running backend

Render's existing `url_media.download` and `audio_to_mp3` processed:
`https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4`.

- Download: 1,128,375 bytes; actual audio and video streams.
- FFmpeg MP3 output: 122,834 bytes.
- Both temporary files removed afterward.

No `/transcribe` API or cloud/paid provider was called. The result confirms that
the deployed downloader's direct-file path and FFmpeg work independently of the
TikTok extractor failure. Original upload and manual-clipping tests also pass.

Original `/clip_multi` turned a `MediaError` into a generic 500. Local code now
returns 422, consistent with `/transcribe`, and records no usage after a failed
download. This response correction is not presented as the download fix itself.

## Why verification can appear stuck

Render logs show the old empty-multipart-parser crash at 19:15:50. Deployment
started at 19:16:30 and the new instance started at 19:16:49. On that new instance,
checks at 19:17:37, 19:17:41, 19:17:46, 19:17:50, 19:17:54, 19:17:58, 19:18:42,
19:18:46 and 19:18:50 returned HTTP 200. Thus the old parser crash is not an
accurate explanation for all current verification symptoms.

HTTP 200 is not proof of paid status: this endpoint also returns a completed trial
or denied-access decision. The response body/account/customer mapping cannot be
inferred from the access log. The relevant account email/payment record remains
unverified; no other customers' records were inspected to guess the user's account.

Two further defects were reproduced and fixed locally:

1. Five immediate focus events launched five concurrent verification requests.
   Requests now share the in-progress check; the same browser test launched one.
2. Verification had no deadline, including session lookup, fetch and response JSON
   parsing. A hanging operation could leave `Verifying subscription` indefinitely.
   A 25-second deadline now aborts the fetch and presents Retry without requesting
   another payment or granting paid access.

The request generation/unmount guards remain; delayed auth callbacks cannot restart
checks after cleanup. Backend Stripe verification runs off the async request loop
so its synchronous network calls do not block that loop. A frontend timeout does
not claim to cancel an already running Stripe SDK operation on the server.

No entitlement rule, Stripe price/key, Supabase credential, session verification,
webhook signature check or invoice/customer/product matching was weakened. No
paid access was manually granted. This fixes client waiting/concurrency, not an
unverified assertion that the user's payment has been correctly linked.

## Tests and readiness

- 76 backend tests passed: URL transport/media, auth/ownership/usage, original 1–5
  clipping contracts, verified billing, and failed-download 422/no-usage behavior.
- 18 frontend tests passed, including hung session lookup, fetch and JSON parsing,
  paid/trial header states and verification-only actions for uncertain access.
- Frontend Vite build passed using dummy loopback configuration; no `.env` edits.
- Local browser: five focus events produced five requests before the patch and one
  afterward. A deliberately hung loopback billing request ended by the 27-second
  observation (25-second configured deadline), showed Retry, cleared the spinner,
  and did not open an upgrade modal. Fetch was restored after the test.
- Local yt-dlp browser targets are available after the dependency install.
- Live Render direct MP4 and FFmpeg conversion passed; TikTok extraction failed.
- A full Docker rebuild/new Render image and real-account paid-access test were
  not run. These remain required before treating this as production-ready.

The subsequent local UI fix removes the unconditional Upgrade button for verified
paid accounts and existing Stripe trials. Trial access responses identify verified
existing subscriptions without claiming payment or bypassing quotas. Reaching an
existing subscription's trial quota does not request another subscription. Private
production billing-audit details are stored under ignored local data, not in Git.

Changed backend files: `app.py`, `stripe_billing.py`, `requirements.txt`, `tests/test_studios_config.py`,
`tests/test_verified_billing.py`, and this report.
Changed frontend files: `src/Clipper.jsx`, `src/billingClient.mjs`,
`tests/billing.test.mjs`.

All five navigation sections, original Transcribe/Clip controls, working media
processing and earlier subscription-security fixes remain. No deployment was made.
Official dependency reference: https://github.com/yt-dlp/yt-dlp#impersonation
