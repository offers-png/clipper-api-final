# AI Video Generator update

The existing Video Editor workspace now supplies one AI Video Generator with
Text-to-Video and Image-to-Video tabs. `/workspace/generator` is a navigation alias
for the same retained editor iframe, preserving its timeline and the five existing
sections. Existing image-video API URLs remain valid; `/video-generator/*` aliases
share their authentication, ownership, usage, queues, job results and editor assets.

## Server-side model allowlist

Official KIE documentation and pricing verified 2026-10-26:

- https://docs.kie.ai/market/wan/2-5-text-to-video
- https://docs.kie.ai/market/wan/2-5-image-to-video
- https://docs.kie.ai/market/wan/2-7-text-to-video
- https://docs.kie.ai/market/wan/2-7-image-to-video
- https://kie.ai/wan-2-5 — 720p: 12 credits/second (5s 60; 10s 120).
- https://kie.ai/wan-2-7-video — 720p: 16 credits/second (5s 80; 10s 160).

All four adapters support landscape/portrait. Legacy square output and supported
2–15-second Wan 2.7 requests are retained. Wan 2.5 is limited to 5/10 seconds.
Official payload differences (string duration and aspect_ratio/image_url for 2.5,
integer duration and ratio/first_frame_url for 2.7) are enforced. No keys are sent
to the frontend. The existing Render key is reused, with no new credentials.

Account quotes bind model, mode, duration, ratio, source, prompt and motion.
Persistent quote metadata is migrated additively without resetting historical
spending. The same owner-only controls, USD 1 total cap, balance checks, explicit
confirmation, atomic reservation and no paid retries remain. At prior USD .40
exposure, 10-second Wan 2.5 fits the remaining USD .60; 10-second Wan 2.7 does not.
The exact credit estimate is displayed before review, and a fresh server quote
shows current balance/allowance before submission. Provider progress uses actual
KIE task states. Completed media is validated, normalized to MP4 and handed to the
existing editor; no animation or placeholder output substitutes for AI generation.

## Bounded Qwen input

The installed tokenizer divides long inputs into ordered, lossless sections,
preferring word boundaries. Every section is analyzed serially. Results are merged
by the original timestamp-backed candidate ID to avoid duplicates. Evidence remains
source-grounded; clip boundaries and source coverage are unchanged. Thumbnail
planning also falls back to bounded moment ranking when its prompt exceeds budget.
The editing assistant also analyzes ordered sections, returns section-labeled answers, and uses an authenticated, owned background job so long requests can be polled. The accepted transcript limit increases to 120,000 characters. Existing synchronous URLs remain compatible. Existing context/memory settings and safeguards remain in place.

## Local verification

146 backend tests, 31 frontend tests, five real-media integration tests and Vite
builds passed. Simulated KIE transports cover all four model/mode combinations,
FFmpeg result validation, pricing, incompatible models, ratio/model swaps, quotes
without submission and confirmation requirements. These media fixtures are not
claimed to be synthesized AI videos. A real local Qwen stress input (repeated
recorded-dialogue text, explicitly not a video transcript) processed three sections
and merged to one source candidate without context overflow. Existing real spoken
20-second video clipping/editing regressions passed. Local browser tests verified
both tabs and one shared iframe retaining work across generator/editor navigation.

Paid API submissions are prohibited during release testing. Live quotes, UI,
authorization and preservation of the spending cap must be verified separately
after deployment. Actual KIE synthesis/latency/output quality remain untested by
this release unless a separate spending confirmation is provided.

A second real-Qwen assistant stress test counted 1,793 input tokens (above the former 1,250-token rejection) and completed six ordered sections with the existing Qwen3-0.6B model. No paid or external analysis API was used.
