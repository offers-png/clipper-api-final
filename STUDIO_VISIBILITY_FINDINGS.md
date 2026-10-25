# Studio visibility investigation and local fixes

## Observed deployment

Read-only browser checks used the existing public site and backend. No deployment, production login, account mutation, paid generation, or Render setting change was performed.

| Check | Observed result |
|---|---|
| `https://clipper-frontend.onrender.com/` | Original sign-in screen; no Studio navigation. |
| Deployed entry JS `/assets/index-fy4Ma2vS.js` | No Studio link text or `/workspace/auto` route marker. |
| `/studios/studio.html` and `/studios/bridge.js` | HTTP 200: Studio assets are present. |
| `/workspace/auto` | HTTP 200 SPA document: deep-link rewrite is already working. |
| `https://clipper-api-final-1.onrender.com/api/health` | HTTP 200 JSON: original backend is reachable. |
| Same backend `/studios-api/health` and `/studios-api/studio/capabilities` | HTTP 404 JSON: the deployed Studio API is not available. |
| Render Dashboard | Login page; no authenticated configuration session was available. Actual dashboard variable values/build settings were not read or changed. |

Source inspection explains the visibility failure: `StudioEntry` required `VITE_ENABLE_STUDIOS === 'true'`; absent/false compiled away the navigation and selected only the original app. The deployed bundle is consistent with that effective flag-off build. Dashboard access would be needed to distinguish a missing flag from an explicitly false flag or inspect its exact deploy/build configuration.

There were additional integration gaps: the API URL required a separate Studio variable with no existing-backend fallback; Studio iframe fetch/XHR/media requests did not carry a signed-in session; and the backend installer was disabled by default and explicitly loopback-only. Simply exposing the links or removing that loopback check would not safely make the deployed workflows usable.

## Local changes prepared

- Navigation is always visible and remains sticky above the original header. The header is offset by the measured navigation height, including on wrapped mobile layouts. Missing/false legacy `VITE_ENABLE_STUDIOS` no longer hides routes. Original ClipForge, Auto Clip Studio, Transcript Clip Studio, Thumbnail Studio and Video Editor are accessible through the existing application.
- Existing `src/App.jsx`, `Clipper.jsx`, `ProtectedRoute.jsx`, `AuthForm.jsx`, `Dashboard.jsx` and `supabaseClient.js` remain unchanged. Transcribe/Clip tabs and manual 1–5 controls are preserved.
- The Studio URL falls back to the existing `VITE_API_BASE` and its existing known backend fallback, with `/studios-api` appended once. A stale localhost override is not used on a deployed host when the existing backend URL is valid.
- Deployed Studio pages always use the existing auth guard. The offline flag works only on loopback hosts. Protected-route redirects correctly return to the original login UI; switching accounts resets retained Studio frames.
- Session tokens travel only in same-origin parent/iframe messages and backend Authorization headers. They are not put in URLs or copied into new persistent storage. Private previews, posters and downloads are fetched with authorization and displayed as browser blob URLs. Editor handoffs retain the original server resource URL internally.
- Public-mode Studio APIs now validate the user's JWT with the existing Supabase SDK `auth.get_user(jwt)`, require per-user ownership of sources/jobs/files, and scope local history/thumbnail lists. No Supabase schema/configuration changes are needed. Resource ownership and usage reservations use a private SQLite file in the Studio data directory; do not overlap it with legacy public media storage.
- Rendering requests use the existing `check_clip_access` / `record_clip_used` functions with the verified email, not a caller-supplied identity. Async reservations prevent parallel trial-limit bypass. Failed jobs release their reservation; successful batches record one usage, matching the original batch semantics. Billing unavailability fails closed for the new API.
- Original auth/billing/Stripe/data-helper modules and original backend endpoints were not rewritten. This is additive protection of the new Studio API, not a claim that every legacy endpoint has undergone a security audit.
- Core Studio routes can import without optional speech dependencies. Optional `httpx` constraints were corrected to match the existing Supabase production pin. Model-dependent controls report availability honestly; missing models do not trigger downloads or mock results.

## Tests actually run

- **15 backend access/configuration tests passed in 3.087 seconds.** Missing/invalid tokens, cross-user access, history/media ownership, billing denials, unavailable billing, async trial reservations, failed-job accounting, SDK token validation calls, optional-dependency-free startup, and original-file preservation were checked locally.
- **Five actual-video/media regressions passed in 81.957 seconds.** Actual local Whisper/Qwen, manual counts 1–5, transcript selection, Full Video coverage, FFmpeg timeline editing, audio mix/mute/volume, previews, MP4/ZIP downloads and provider-unavailable behavior were exercised.
- **Four frontend settings tests passed.** Existing API fallback, explicit override, no duplicate API prefix, production offline-bypass prevention and stale localhost override handling were checked.
- Vite build passed with `VITE_ENABLE_STUDIOS=false`, no Studio URL override, and offline mode false. Test-only localhost auth configuration was used; no production credentials were loaded. Existing browser-support/module-metadata warnings remain.
- Browser contract tests used an explicitly local mock auth/billing server under `tests/serve_studio_contract.py`, not real Supabase/Stripe accounts. The file is excluded from Docker runtime context. The real Studio rendering/transcription code and actual local video were used behind that fixture.
- An unauthenticated Studio deep link returned to sign-in. All navigation links were visible. Original Transcribe and Clip tabs remained, and Add Clip stopped at five rows. After scrolling, the navigation stayed at y=0 and the original header stayed immediately below it.
- Authenticated Auto Full Video produced three clips; private video/poster blob URLs loaded and playback advanced. Editor import retained state, splitting/rendering produced a 2-second MP4, and its authenticated download returned 200.
- Thumbnail Studio loaded three owned clip covers and exported PNG/JPEG at 1280×720. This is manual composition, not a claim of new AI image generation.
- Transcript Studio recognized 48 words; selected 0.040–2.336 seconds with zero padding; its private preview was 2.299675 seconds, played, downloaded (200), and imported into the retained editor timeline.
- Browser negative cases: missing/invalid session returned 401; denied billing returned 403; three completed render batches recorded three fixture usages.

## Before an approved deployment

1. Review these local branches: frontend `fix/studio-navigation-20261025`, backend `fix/studio-access-20261025`. Publication to these feature branches is approved; merging and deployment remain separate approval steps.
2. Confirm actual Render source branches/build commands using an authenticated dashboard. The public frontend's existing SPA rewrite works; no rewrite change was made.
3. Deploy the reviewed backend code with explicit `CLIPFORGE_ENABLE_STUDIOS=1` only after approval. Public mode is the default; keep `CLIPFORGE_STUDIOS_LOCAL_ONLY=0`. Existing Supabase/Stripe variables are reused unchanged. Configure an approved writable Studio data directory that is separate from legacy public media directories.
4. Deploy the reviewed frontend. A Studio URL override is optional; existing backend configuration is reused. Do not set deployed offline mode. Navigation no longer requires the deprecated Studio visibility flag.
5. Verify authenticated flows with a real signed-in deployment test account, ownership isolation, and billing results. That live-account verification was not performed here. The current deployed backend still returns 404 for Studios, so end-to-end production success cannot be claimed before an approved release.
6. AI transcription/highlights additionally require the existing model weights, compatible runtime/dependencies and adequate memory on the deployment host. Models are not bundled/downloaded automatically, and their availability on the deployed service could not be inspected while its Studio API is absent. Full Video/manual editing do not require those models after these fixes. No paid hosting plan or model download was activated.
7. Keep paid KIE generation disabled. No KIE paid flag, key, provider selection or credential was changed, and no paid request was executed. The KIE backend feature branch is separate from the currently deployed main code.

## Legacy security boundary

The original backend contains pre-existing caller-supplied/anonymous identity paths in `/clip_multi`, and its helper `auth.py` decodes JWT payloads without verification. Those original modules were deliberately preserved. The new Studio middleware does not use that unverified decoder: it validates via Supabase. Do not interpret the tests above as certification of all legacy public API authorization; hardening those original paths requires a separate reviewed change while preserving the original UI.

## Changed files

Frontend: `.env.example`, `src/StudioEntry.jsx`, `StudioGuard.jsx`, `StudioWorkspace.jsx`, `studios.css`, new `StudioNav.jsx` / `studioSettings.mjs`, `public/studios/bridge.js`, `editor-handoff.js`, `studio.html`, frontend settings tests and integration notes.

Backend: `.env.example`, `.dockerignore`, new `clipforge_studios/access.py`, `install.py`, `dev.py`, `backend/studio.py`, `backend/transcription.py` (lazy optional import only), `requirements-studios.txt`, access/configuration tests, local browser fixture and integration notes. Production credentials, original application code, billing implementation, Render blueprint and main branches remain untouched.
