# Thumbnail Creator review - 2026-10-26

Development branches in both repositories: `feat/thumbnail-completion-20261026`. Code is staged for review, not pushed or deployed. The approved free model/runtime downloads were completed locally and on the existing Render disk. Public production settings, paid-generation restrictions, credentials, and the deployed revision remain unchanged.

## Implemented workflow

1. Upload actual video through the existing Studio source API.
2. Recognize dialogue offline with faster-whisper base.en in an isolated process. Its exit releases native memory before other models load. Other transcription/clipping workflows keep their existing implementation.
3. Detect scene changes and describe sampled real frames with local SmolVLM. These are uncertain visual observations, not identity/emotion detection or claims of examining every frame.
4. Prepare three creative directions with local Qwen. Speech-backed titles/evidence must match actual timestamped phrases. Each successive schema excludes previously selected titles. Longer transcripts are ranked in bounded batches instead of truncating to the beginning. Silent videos use visual observations and explicitly label proposed titles as headlines, not spoken dialogue.
5. Extract reference images from each chosen moment, rather than three unrelated evenly spaced positions.
6. The existing backend KIE adapter can render three separate briefs when explicitly authorized. Paid image requests remain disabled; extracted reference frames are always labelled as references, never AI-generated thumbnails.
7. Preview, Select, Edit, and Download remain in the existing Thumbnail Creator. Selection loads separate image/shape/text layers into the existing manual canvas. PNG/JPEG platform exports remain intact.

Browser jobs resume after refresh. Missing speech, text, vision and image-provider capabilities are reported separately. Interrupted/failed paid jobs cannot automatically resubmit; partial images are preserved. Completed cached results remain available even when generation is disabled.

## Models and server preparation

Read-only inspection established a 2 GiB cgroup allocation, no installed Studio models/speech library, and a Windows-only runtime path. Existing Qwen3-4B weights are 2,497,280,256 bytes and unsuitable for this allocation.

User approved only free model/runtime/dependency downloads. The following publisher files were downloaded and checksum-verified:

- Qwen3-0.6B Q8_0: 639,446,688 bytes.
- SmolVLM-256M Q8_0: 175,054,528 bytes.
- SmolVLM f16 projector: 190,031,616 bytes.
- Linux llama.cpp CPU runtime b11669: 17,851,611 bytes.
- Existing base.en model family reused on Windows and downloaded for the server, with pinned publisher revision/hash verification.

On Render, files are staged under `/data/clipforge-models`. Transcription dependencies are installed in `/data/clipforge-models/speech-env` with system-site-package visibility; production Python packages were not changed. The Linux runtime starts successfully and supports the required loading flags.

The staged Linux source was tested separately under `/data/clipforge-thumbnail-review/source`; it does not replace `/app` or alter public routes. The probe ran without a KIE key or billing configuration. It recognized actual JFK speech and actual red-flower video frames, then generated transcript-backed concepts. These were independent fixtures; no relationship between the speech and flower scene was invented.

Measured Linux text-runtime peak RSS: **967,557,120 bytes (about 923 MiB)**. Minimum available container memory: **998,682,624 bytes (about 952 MiB)**. The 2 GiB container limit and existing startup/stop thresholds were retained. Cgroup availability correctly accounts for reclaimable inactive file cache; no caches, OS limits or hosting plan were changed.

After code deployment approval, these server-only paths will connect the staged files to the public Thumbnail Creator:

```
CLIPFORGE_MODELS_DIR=/data/clipforge-models
CLIPFORGE_LOCAL_MODEL=/data/clipforge-models/base.en
CLIPFORGE_THUMBNAIL_SPEECH_PYTHON=/data/clipforge-models/speech-env/bin/python
CLIPFORGE_ANALYSIS_MODEL=/data/clipforge-models/studio/Qwen3-0.6B-Q8_0.gguf
CLIPFORGE_LLAMA_SERVER=/data/clipforge-models/runtime/llama-b11669/llama-server
CLIPFORGE_VISION_MODEL=/data/clipforge-models/vision/SmolVLM-256M-Instruct-Q8_0.gguf
CLIPFORGE_VISION_PROJECTOR=/data/clipforge-models/vision/mmproj-SmolVLM-256M-Instruct-f16.gguf
```

No automatic downloads run at application startup. Keep global KIE spending off and preserve the completed account-only video-test guard until a new image-generation approval is supplied. That prior video allowance does not authorize images. KIE's last verified credit balance was zero; no top-up or paid request was made in this update.

## Verification

- **107 backend tests passed**: phrase/evidence alignment, model readiness, cgroup limits/cache accounting, isolated speech readiness, missing-vision behavior, restart handling, paid retry rejection, existing KIE protocol/three-image/media contracts, account budget protection, Studio authentication/ownership, URL and subscription regressions.
- **20 frontend regression tests passed**, JavaScript syntax check passed, and Vite production build passed with process-only loopback/dummy configuration. Existing toolchain/browser-metadata warnings remain.
- Actual 20-second video upload passed with offline base.en recognition, real SmolVLM observations, real Qwen3-0.6B concept generation, three distinct transcript-backed titles, matching moment timestamps, actual reference JPEG extraction/downloads, and zero paid requests. Initial 4B runs stopped safely at the RAM threshold; initial smaller-model duplicate titles were rejected, then fixed through distinct-title schemas and verified in subsequent actual runs.
- Linux inference probe passed on the actual Render allocation as described above. Public backend health remained HTTP 200 with authentication required and paid-generation auto-enable false.
- Browser checks in the existing unified local app displayed all three actual-video concepts with loaded reference frames. Selecting the actual concept 'They told it to you.' loaded it into image/shape/text layers and exported PNG (419,948 bytes) and JPEG (60,211 bytes) without errors. Separate UI-fixture checks verified text/color/X/Y controls, selection, reference HTTP downloads, Esc close, YouTube 1280x720, TikTok 1080x1920, Instagram 1080x1080, Facebook 1200x630, and custom 800x450 output.
- Provider HTTP was blocked in local contract tests. Actual KIE image synthesis, final provider output quality, and a public production end-to-end run remain **unverified** because no paid-image approval exists. Vision can misread on-screen captions; observations and briefs are shown for review and are not treated as verified identities or facts.
- Actual silent 5-second video integration also passed: real visual observations, three Qwen creative approaches, source-frame timestamps and downloads, no fabricated transcript, and zero paid requests. Result is recorded separately in the ignored data reports (`thumbnail-silent-test.json`).

Local review URL: http://localhost:5174/workspace/thumbnails (loopback-only test API on port 5193). The gallery currently shows real-video concepts and reference frames, not AI-synthesized images.

## Files and remaining work

Backend: `thumbnail_ai.py`, new `thumbnail_speech.py` and `thumbnail_vision.py`, `studio_ai.py`, `studio_memory.py`, `transcription.py`, `.env.example`, public model-preparation tools, and thumbnail contract/real-video/Linux/UI test helpers. Frontend: `public/studios/thumbnail-generator.js`. Original app, Stripe, Supabase, authentication, pricing, production credentials, Dockerfile and production requirements remain unchanged.

Remaining: approve code deployment and apply model paths; separately approve a bounded KIE three-image test with available credits; verify actual generated-image quality, preview, editing, and downloads end-to-end before claiming production readiness. Longer-video performance and broad visual recognition quality still need representative user-video testing.

Publisher sources: https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/tree/main ; https://huggingface.co/ggml-org/SmolVLM-256M-Instruct-GGUF/tree/main ; https://github.com/ggml-org/llama.cpp/releases/tag/b11669

## Authorized production rollout follow-up

The user subsequently approved feature-branch publication, deployment and model-path configuration, with paid KIE requests explicitly prohibited. Initial revisions: backend `abb100c`, frontend `501ef59`. Both deployed successfully; authenticated speech/text/vision readiness became true, image generation remained disabled, and anonymous provider capabilities returned 401. A live 11.28-second MP4 upload succeeded and recognized 22 actual JFK speech words, with timestamps from 0.17 to 10.29 seconds.

The first live concept phase hit the unchanged 768 MiB safety floor. The public app had more baseline memory than the isolated probe, and closed predecessor weights remained charged as active file cache. The follow-up fix advises away only the application's closed model files on Linux (no global cache dropping or OS setting changes), uses smaller compute batches and quantized ephemeral KV cache for sub-1-GiB models on <=2-GiB containers, and forcibly stops only the owned model process if graceful termination stalls. Context remains 2048, CPU inference remains serial, and other machines retain the original settings. New regression cases bring the backend suite to 111 passing tests. Final stress/live retest results are recorded in the deployment report; no KIE paid request is part of them.

The corrected isolated Linux probe passed with an extra 96 MiB retained test allocation alongside the public app. It recognized actual speech and flowers and produced three concepts. Peak text-runtime RSS was 856,121,344 bytes (about 816 MiB); minimum available memory was 913,215,488 bytes (about 871 MiB), above the unchanged 768 MiB threshold. Effective small-container profile: 2048 context, 2 CPU threads, batch 32, microbatch 8, serial inference, q8_0 KV cache, flash attention. No additional models or paid APIs were used.
