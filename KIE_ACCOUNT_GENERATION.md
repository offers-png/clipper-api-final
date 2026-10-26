# Account-scoped KIE generation

This adds account-specific image and image-to-video access without altering Stripe,
Supabase, login, prices, or Classic routes. Work remains on the existing feature
branch; no main merge is needed for a specific-commit Render deployment.

Server settings (actual credentials remain in Render):

- `CLIPFORGE_KIE_ACCOUNT_MODE=1`
- `CLIPFORGE_KIE_ALLOWED_USER_IDS`: verified authenticated account UUID allowlist
- `CLIPFORGE_KIE_SPENDING_LIMIT_USD`: total exposure limit per allowed account
- `CLIPFORGE_VIDEO_PROVIDER_API_KEY`: existing server secret, unchanged
- `CLIPFORGE_STUDIO_DATA_DIR`: persistent disk directory for spending/ownership ledgers

The global paid switch stays disabled. The previous one-shot ledger is retained
and included in exposure; new quotes do not reset earlier spending. The approved
USD 1 total cap therefore leaves USD .60 after the previous USD .40 video test.

## Safety and interface

Quote endpoints read the official KIE credit balance without submitting generation
tasks. Server-priced quotes expire after ten minutes and bind the account, source,
prompt/concepts, duration, ratio and motion. Generation requires an explicit quote
ticket and spending confirmation. Reservations are atomic and persistent. Each
provider task is claimed before HTTP submission; failed or uncertain paid tasks
are never automatically retried. Uncertain tasks block further submissions pending
manual reconciliation. No generated result is substituted with a reference frame.

Prices checked against the official KIE pages on 2026-10-26:

- https://kie.ai/nano-banana — four credits per image; three images = 12 credits,
  approximately USD .06.
- https://kie.ai/wan-2-7-video — 720p at 16 credits/second; five seconds = 80 credits,
  approximately USD .40.
- https://docs.kie.ai/common-api/get-account-credits — read-only balance endpoint.

The frontend uses a shared confirmation dialog for both generation workflows.
Insufficient balance or allowance disables confirmation. Cancel, Escape and
outside clicks never authorize generation. Three thumbnail results retain the
existing preview, selection, layer editing and PNG/JPEG export workflow.

## Other production connections

Auto Clips and Text-to-Clip now use the already installed isolated speech Python,
as Thumbnail Creator does. Children inherit no API credentials, remain offline,
and exit before text analysis. Existing memory safeguards remain unchanged.
Small-model highlights use one candidate at a time with exact source-quote grammar
and category-specific criteria; unsupported content is still allowed to return no
highlights. Selection, sentence boundaries and FFmpeg rendering are unchanged.

The outdated blanket model/provider banner was removed in favor of the existing
feature-specific capabilities and errors. Studio status styling is neutral blue;
thumbnail mojibake is repaired, Studio body backgrounds are bright, and changed
module/iframe versions prevent stale generation interfaces. The original Classic
implementation and authentication/billing files are unchanged.

## Verification boundary

Local account tests cover allowlisting, historical spending, fresh balance reads,
atomic concurrent reservations, quote binding/expiry/confirmation, exactly three
image tasks, no fourth task, one video task, failed/uncertain requests, no automatic
retry, and credential redaction. Provider protocol responses in these tests are
explicit test fixtures, not evidence of image synthesis. No paid KIE submission is
performed for release verification. Production balance, quote-only checks and
free real-media verification must be reported separately after deployment.

Local release checks: 127 backend contract/regression tests passed, 24 frontend tests passed, and the Vite production build passed. Five real-media integration tests passed against a 20-second spoken MP4, including 1?5 manual clips, full-source coverage, actual transcript selection, grounded speech highlights, captions, thumbnails, ZIP/MP4 downloads, music/effects, mute and independent volumes, trimming/reordering, intro/outro images, overlays and fades. The first run exposed an incorrect test data-directory setup and an overly restrictive small-model prompt; both were corrected before the passing run. Browser checks verified a bright 390px mobile layout and a 366px video modal, with no horizontal overflow. No paid KIE requests were submitted.
