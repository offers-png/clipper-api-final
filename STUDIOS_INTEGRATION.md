# Unified ClipForge Studio integration (feature branch only)

This branch extends the existing `offers-png/clipper-api-final` backend. Pair it with the same `feature/unified-studios-20261025` branch of `offers-png/clipper-frontend`.

The original production application, Stripe integration, Supabase authentication/history, OpenAI integration, dependency pins, Dockerfile and Render blueprint are retained. `app.py` adds only an opt-in installer after the existing routes. No original business logic is replaced. The installer is inactive unless `CLIPFORGE_ENABLE_STUDIOS=1`.

The new API is mounted at `/studios-api`. This avoids conflicting with the original clipping, billing and media routes. While this is a development integration its API rejects non-loopback clients. Do not enable it publicly by changing proxy headers or removing that check; production authentication/authorization, billing quotas, storage ownership and resource limits need an explicit deployment review first.

## Included features

- Actual faster-whisper word-level transcript selection and local FFmpeg clipping.
- Full Video coverage including the final remainder; local Qwen transcript highlights, categories, counts and editing toggles.
- Original manual 1–5 clipping retained; separate offline companion tools included.
- FFmpeg timeline editing: trim, split, reorder, combine, intro/outro images, overlays, text/captions, source audio controls, music/effects mixing, fades and MP4 export.
- Thumbnail canvas/project editor; actual-video concept planning, three grounded design concepts, reference frames and an explicit unavailable image-generation state.
- Image-to-video interface and server-only provider contract. No provider is invented, and no image animation is labelled AI generation.

No models, user video, audio, generated images, transcripts, environment credentials or model binaries are committed. The existing local model installation can be reused through `CLIPFORGE_MODELS_DIR`. Linux model runtimes and equivalent memory safeguards require validation before any production launch; current local inference was tested on Windows CPU.

## Local launch

Use existing compatible Python dependencies or install `requirements-studios.txt` in an isolated local environment. This does not change the original production requirements. No model download is performed by the code.

```powershell
$env:CLIPFORGE_ENABLE_STUDIOS='1'
$env:CLIPFORGE_MODELS_DIR='C:/path/to/existing/models'
$env:CLIPFORGE_STUDIO_DATA_DIR='C:/path/to/local/studio-data'
python -m uvicorn clipforge_studios.dev:app --host localhost --port 5175
```

The credential-free development host does not import production database/OpenAI/Stripe clients. Production's normal `app:app` entry point is unchanged when the flag is off.

## Future provider configuration

No image-to-video vendor, existing vendor key-variable name, API URL or model ID was found in either current repository. The user confirmed the provider will be chosen later. There is no guessed vendor-specific HTTP implementation.

New, provider-independent **server-side** variables:

| Variable | Purpose |
|---|---|
| `CLIPFORGE_VIDEO_PROVIDER_FACTORY` | Installed adapter factory, e.g. `your_adapter:build_provider`; blank keeps generation unavailable. |
| `CLIPFORGE_VIDEO_PROVIDER_CONFIG` | JSON object of non-secret adapter settings; `{}` by default. |
| `CLIPFORGE_VIDEO_PROVIDER_API_KEY` | Optional generic secret forwarded only to the factory; blank in `.env.example`. This is a new integration variable, not a claim about an existing Render key name. |

The adapter factory receives `api_key=...` and `config={...}` and implements `status()` and `generate(image_path, request) -> bytes`. It declares supported durations/ratios. Actual returned video is checked for type, size, duration and ratio, then normalized to MP4 and registered with the editor. Provider failures fail closed without returning credential-bearing exception details. Invalid adapter configuration never enables fake generation.

The selected vendor may require a different credential format, model identifier, endpoint, polling/webhook protocol, timeout/cost controls and output-download allowlist. Those must be implemented and tested against that vendor's actual documentation later. No API keys are required in frontend code, and existing Render environment values are not changed by this branch.

## Tests and production boundary

Use `CLIPFORGE_TEST_VIDEO` to point to an actual local spoken video, and `CLIPFORGE_TEST_BASE=http://localhost:5175/studios-api`. Run `python -m unittest tests.test_studios_media tests.test_studios_config -v` against the local host. Media tests explicitly skip when the actual fixture is unavailable; they do not substitute mock transcripts.

Local media tests passed for five test cases in 85.394 seconds, including actual local Whisper/Qwen, manual counts 1–5, Full Video, transcript selection, video editing, audio mixing, imports, previews/ZIPs and unavailable-provider behavior. Six configuration/preservation tests passed in 0.904 seconds. Original auth, billing, Stripe, data helpers, Render/Docker and dependency files matched `origin/main`; original `app.py` remained an unchanged prefix followed only by the gated installer.

The paired built frontend was exercised against this prefixed API on localhost:5174/5175. Auto Clip produced three real clips and sent one to the retained editor. Thumbnail analysis produced three grounded concepts from actual dialogue; Edit loaded separate image/text/background layers and PNG/JPEG exports decoded at 1280×720. Actual image synthesis remained explicitly unavailable. Browser checks caught and fixed XMLHttpRequest upload routing under the API prefix.

`python tools/scan_staged.py .` checks staged files without printing matched credential values. Before commit, the backend and frontend staged sets passed this check, excluded `.env` variants/media/models, and passed `git diff --cached --check`. This is a conservative pattern/artifact scan, not a guarantee against every possible secret format.

Neither authenticated cloud behavior nor real image/video synthesis was exercised. Before deployment: review authorization/billing for new APIs, choose and implement actual providers, validate persistent per-user storage/job execution, validate Linux FFmpeg/fonts/model runtime and memory safety, and obtain deployment approval. This branch does not change Render settings, merge main, open deployment previews or deploy anything.
