# KIE AI adapters — integration review

## Current connection investigation (2026-10-26)

Live authenticated capability checks returned the generic unavailable image/video providers, not KIE. The deployed backend omitted the earlier KIE feature commit. Render's environment page contains the existing `CLIPFORGE_VIDEO_PROVIDER_API_KEY` variable; neither provider factory nor the paid-request opt-in is present. Only variable names were inspected; the secret value was not read or changed.

Branch `fix/kie-provider-connection-20261026` reapplies the earlier adapters to the current backend, preserving newer URL downloading, subscription handling and Studio access guards. Blank factories select built-in KIE from the existing server key. Capability messages distinguish configured credentials from approved execution and do not claim remote authentication has been tested. Production remains unchanged until this backend update is deployed.

Current verification: 88 backend tests and 20 frontend tests passed; the existing frontend production build passed. KIE HTTP is blocked throughout provider tests. Tests exercise official payload construction, uploads, polling, error redaction, three-image background rendering, downloads and video normalization/editor import with FFmpeg-generated fixtures. An added HTTP capability/approval test verifies that configured adapters remain unavailable for paid execution. The optional image smoke tool's default dry run reported zero network requests. No actual KIE image/video generation or remote authentication test ran, and no API credits were spent.

Thumbnail video-to-concept planning still requires the separately unavailable server Whisper/Qwen models. Connecting an image-generation adapter does not supply transcript analysis or semantic vision; the full automatic video-to-thumbnail workflow is not yet production-ready. Existing manual Thumbnail Creator editing remains intact. No model download, production setting change, merge or deployment was performed in this investigation.

These changes connect the existing video-provider and thumbnail-provider contracts to KIE. Existing authentication, billing, Supabase/OpenAI integrations and production credentials are unchanged. The previous local-only Studio API restriction remains in place; this update does not authorize production activation.

## Official API contracts used

- [KIE file-stream upload](https://docs.kie.ai/file-upload-api/upload-file-stream): backend multipart upload with Bearer authentication; use the returned `data.downloadUrl`. Uploads are temporary, so source files remain in ClipForge storage.
- [Wan 2.7 image-to-video](https://docs.kie.ai/market/wan/2-7-image-to-video): `wan/2-7-image-to-video`, `first_frame_url`, integer duration 2–15 seconds, and 720p output. No undocumented `aspect_ratio` field is sent. The reference is fitted/padded to the user's chosen ratio before upload; returned output dimensions/duration are checked before importing it into the editor. Actual provider behavior still requires a live approved test.
- [Nano Banana Edit](https://docs.kie.ai/market/google/nano-banana-edit): `google/nano-banana-edit`, actual reference `image_urls`, explicit `aspect_ratio`, and PNG output. Each of the three existing transcript-grounded concepts gets its own request. These are genuine provider requests when enabled, not extracted frames relabelled as synthesis.
- [Get Task Details](https://docs.kie.ai/market/common/get-task-detail): create tasks through `/api/v1/jobs/createTask`, poll `/api/v1/jobs/recordInfo`, and consume `resultJson.resultUrls` only after `state=success`. An accepted task is not treated as a finished generation. Failures/timeouts do not automatically submit another paid task.
- [Imagen4 Fast](https://docs.kie.ai/market/google/imagen4-fast): used only by the optional single-image smoke-test tool. Its payload creates one image at 1:1 without reference uploads or video generation.

No model downloads or new dependencies were added. Requests and FFmpeg already exist in this project.

## Configuration and approval

The backend reads the **existing** `CLIPFORGE_VIDEO_PROVIDER_API_KEY` variable. Do not paste its value into Git, frontend variables, shell commands, screenshots or reports. No existing Render value was read, changed or replaced during development.

`CLIPFORGE_KIE_ALLOW_PAID_REQUESTS=0` is the default. With the key configured, capabilities show that KIE is configured but generation is disabled pending approval. The adapter checks this opt-in before network operations, including reference uploads. Nothing billable runs during startup or capability checks.

Blank `CLIPFORGE_VIDEO_PROVIDER_FACTORY` / `CLIPFORGE_IMAGE_PROVIDER_FACTORY` automatically choose the built-in KIE adapters if that existing key is present. Explicit factory selections continue to take precedence. Their explicit names are:

```text
clipforge_studios.backend.kie_provider:build_video_provider
clipforge_studios.backend.kie_provider:build_image_provider
```

The existing `CLIPFORGE_VIDEO_PROVIDER_CONFIG` JSON can set `timeout_seconds` (10–1800, default 900). API/upload hosts and supported model schemas are fixed in the adapter; arbitrary API endpoints cannot receive the key through this configuration. No frontend key variable is added.

Live use will require separately approving paid requests and enabling the opt-in in the intended server environment. This report does not request or perform that environment change.

## One-image smoke test — NOT executed live

```powershell
python tools/kie_image_smoke.py
```

This was run locally and returned `dry_run=true`, `network_requests=0`, model `google/imagen4-fast`, one maximum generation submission. It needs no key in dry-run mode.

Only after approval, an operator can supply the existing secret through the process environment, explicitly enable the paid-request opt-in, and run the tool with `--approve-paid-image-test`. It downloads one image, never a three-image batch or video. The tool checks output paths first and does not automatically resubmit a failed task. Current account pricing/credits must be checked against [KIE pricing](https://kie.ai/pricing) before authorizing that live test; no unverified exact price or spending cap is claimed here.

## Security and behavior

- Authorization is added only to the fixed KIE API/upload hosts, never to result CDN requests.
- Provider response messages, headers, key values and connection exception details are not returned or logged.
- Result URLs require HTTPS, approved provider/CDN domains, public DNS addresses, and checked redirects. Downloads have size limits and image/MP4 signature checks, followed by existing FFmpeg validation/normalization.
- Image-to-video remains serial. Thumbnail synthesis now runs in a serial background worker, with browser polling, partial completed-image preservation, and duplicate-request/cached-result guards. This avoids holding a browser request open through three remote generation tasks.
- Existing previews/downloads/editor imports use local stored results, not public provider URLs or provider credentials.
- Native quality, prompt fidelity, live KIE authentication and actual provider billing were not tested.

## Local verification

- **17 tests passed in 2.580 seconds:** 11 KIE protocol/pipeline tests plus six configuration/preservation tests. All live HTTP was blocked in the KIE tests; their returned PNG/MP4 bytes were local fixtures, not AI-generated content. Tests cover approval gating, payload fields, polling/failure/timeout behavior, no automatic paid retries, key redaction, CDN auth separation, SSRF/size rejection, async thumbnail results/duplicate guards, and video-to-editor import.
- **Five actual-video regression tests passed in 95.737 seconds:** manual 1–5 clipping, actual local Whisper/Qwen analysis, transcript selection, Full Video coverage, FFmpeg editing, audio/mute/volume tests, imports and exports.
- Updated frontend Vite build passed. Browser/data-service calls remain to the ClipForge backend; frontend code contains no KIE endpoint or secret lookup. Existing browser-support/module-metadata build warnings remain unchanged.
- The smoke tool's default dry run made zero network requests. **No live KIE upload or paid generation request was executed.**

## Files changed

Backend: `clipforge_studios/backend/kie_provider.py`, `video_provider_config.py`, `image_video.py`, `thumbnail_ai.py`, `clipforge_studios/app.py`, `.env.example`, `tools/kie_image_smoke.py`, `tests/test_kie_provider.py`, `tests/test_studios_config.py`, and this report. The preservation test accounts for the separately approved Docker fix already present in this branch's base.

Frontend: `public/studios/thumbnail-generator.js` (asynchronous job polling), `public/studios/editor.js` (provider-supported duration bounds), and `public/studios/thumbnails.html` (accurate local-editing vs optional cloud-generation wording).

Local branch in both repositories: `feature/kie-ai-adapters-20261025`. KIE changes are approved for feature-branch publication only. No merge, deployment or live paid generation is authorized by this report. The preceding Docker fix is separate: `fix/docker-bookworm-20261025`, commit `fea7504d542539f6ba44cdc14da6d0520b60a10b`, with a successful [Docker build and MP4 smoke-test run](https://github.com/offers-png/clipper-api-final/actions/runs/40228844726).
