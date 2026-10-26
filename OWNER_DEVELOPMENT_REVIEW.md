# Owner development entitlement and alignment correction

One explicit `CLIPFORGE_OWNER_USER_ID` server setting enables unlimited development
access for that verified UUID only. Both Classic and Studio entry points obtain
identity through the existing server session verifier. Owner development is a
distinct entitlement, never a claim of Stripe payment. It does not update Stripe,
Supabase plan/customer records, prices, keys or customer quotas. Customer gates and
resource ownership checks remain enforced. Owner development requests do not
increment trial usage. Checkout returns existing access without starting a new
subscription for the owner.

KIE authorization, durable reservations, the existing USD 1 total spending cap,
historical exposure, fresh credit checks and explicit confirmation remain separate
and unchanged. Owner access cannot bypass those controls. No paid KIE verification
submission is authorized by this release.

The real speech tests reproduced a zero-duration recognized `Memory` word that was
previously dropped and short-word misalignment after VAD speech-island merging.
Speech now aligns on uncut original PCM. VAD still detects silence and supplies
bounded recovery for collapsed words at actual pause onsets; raw model values are
retained. A new alignment version prevents old Auto/Thumbnail transcript caches
from being reused for newly generated jobs. Historical renders are preserved.
Model timestamps remain estimates; this is not a universal frame-accuracy claim.

Local validation passed: 132 backend tests, 30 frontend tests, five actual-media
integration tests, and the Vite build. Actual extracted AAC/MP4 speech re-recognized
`Ask not what your country can do for you.` at zero padding. The actual 20-second
source retains `Memory` with a positive interval (9.696–9.94 seconds) and its
original collapsed 9.94-second model values. Reproduce real speech checks using
`python -m tests.run_alignment_validation --video ACTUAL.mp4 --speech JFK.flac`
with existing local models; the helper downloads nothing.

The frontend shows `Owner development · Unlimited` instead of an upgrade for this
entitlement, without calling it a paid plan. Status GET requests can retry once on
transient failures. All POST requests, including paid generation and quote creation,
remain single-attempt. Production workflow results must be recorded separately
after specific-commit deployment on the existing feature branches, with no merge.
