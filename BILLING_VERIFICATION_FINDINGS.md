# Verified subscription access investigation — 2026-10-25

Status: code fixes tested locally; no push, deployment, production row repair,
charge, new customer/subscription, price change, or credential change performed.
Both local repositories use branch `fix/verified-subscription-access-20261025`.

## What is confirmed

The active backend billing module is `stripe_billing.py`, using the
`clipforge_users` table and its `plan`, `stripe_customer_id`, and
`stripe_subscription_id` fields. Both Original ClipForge and the Studios use
`check_clip_access`. The separate `billing.py` credit helper is not this flow and
was left unchanged.

Verified source defects:

1. Access trusted a stored `plan=active` without retrieving current Stripe state.
   Stale expired/trial/cancelled records were never reconciled after login.
2. Webhooks ignored Checkout completion and successful invoice/payment events.
   A failed invoice marked a customer expired, while successful payment had no
   corresponding recovery handler.
3. Subscription-created events unconditionally marked accounts active, even when
   payment was incomplete. Other events updated only by customer ID and did not
   detect zero-row writes. Out-of-order events could overwrite newer status.
4. Billing and original clipping accepted caller-supplied email/user_id values;
   missing/anonymous user IDs could skip the original billing gate.
5. Original checkout had no existing-subscription guard. The frontend success
   banner trusted `?upgrade=success` and did not verify payment on login/refresh.
6. Email lookup was case-sensitive, and database unavailability granted access.

These defects can explain false upgrade prompts, but they do not establish which
one affected this particular production payment.

## What remains unverified in production

The deployed ClipForge browser is signed out. Stripe Dashboard requires sign-in.
The connected Supabase account does not list the ClipForge project; unrelated
projects were not queried. No production credentials were requested or inspected.
Consequently, the following have NOT been verified:

- The user's login email and whether it matches the payment customer's email.
- Their Checkout session, invoice/payment status, customer/subscription IDs,
  subscription product/price, or live/test mode.
- Actual Stripe webhook deliveries, failures, event selection, destination URL,
  signing configuration, and the corresponding Supabase row/RLS permissions.
- Whether production has these precise source revisions/configuration.

Finishing the account-specific diagnosis requires the login/payment email or
receipt reference and read-only access to the matching Stripe and Supabase
records. Do not send card details, passwords, or API keys. No manual paid access
was granted, and no production records were modified.

## Local repair

- Billing endpoints and original clipping derive identity from a server-validated
  Supabase token with a confirmed email, ignoring submitted identity fields.
  Existing login/signup/OAuth/session implementation is unchanged.
- Access re-reads current Stripe subscription/customer state, verifies the
  configured ClipForge price and customer email, and requires a paid invoice for
  that same customer and subscription before returning a paid plan. A Stripe trial
  is explicitly a trial. Cancel-at-period-end does not remove an otherwise current
  paid subscription's access.
- Missing customer mappings can be recovered from exact matching Stripe customer
  emails or an owned completed Checkout session. Case-insensitive exact Supabase
  matching escapes LIKE wildcards. Ambiguous/different-email accounts fail closed.
  Stripe's email filter itself is case-sensitive; without a known customer/session
  ID, unusual historical email capitalization can still need verified manual review.
- Verified plan/customer/subscription fields are persisted using the existing
  table. No schema/RLS migration was made. A failed/zero-row write is an error.
- Signed Checkout, invoice success/failure, and subscription events re-read current
  Stripe state rather than apply stale payload status. Unmatched accounts or failed
  synchronization return 503 for retry. Invalid signatures return 400.
- Checkout refuses a second existing or pending subscription and refuses checkout
  during a verification outage. No real checkout-creation call was executed.
- Frontend verifies on login, refresh, focus, and session changes, sends bearer
  authentication, and only shows the paid banner after server-verified payment.
  Errors show Retry and do not redirect to checkout. Original and Studio gates
  distinguish pending payment/verification outages from actual upgrade conditions.

Existing environment names remain `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PRICE_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (existing fallback
`SUPABASE_KEY`), and `FRONTEND_URL`. No values, names, prices, or keys were changed.
The existing webhook route remains `/billing/webhook`.

## Tests actually run

- Backend: 60 passed across `tests.test_verified_billing`, `tests.test_url_media`,
  `tests.test_studios_config`, and `tests.test_studio_access`.
- Billing cases include verified payment restoring expired local access across
  repeated checks; real Stripe SDK webhook HMAC verification; stale failed-payment
  event with current paid status; invoice/customer/subscription/price mismatches;
  unpaid/incomplete/past-due states; trial vs paid; failed persistence; no anonymous
  bypass; spoofed email rejection; and duplicate checkout prevention.
- Original manual 1–5 clipping route contracts passed. Rendering is stubbed in those
  specific tests, not falsely described as a real export test. Existing media tests
  run actual local FFmpeg conversion; Studio access/ownership/usage tests also pass.
- Frontend: 10 Node tests passed (billing and Studio settings); Vite build succeeded.
  Existing non-blocking Vite/Browserslist warnings remain.
- `git diff --check` passed in both repositories. A secret-pattern scan of all 13
  changed source/report files passed without printing matched values or credentials.
- Browser: existing built unified application at `http://localhost:5174/clipper`
  tested against loopback-only auth/billing fixtures on port 5182. Verified login,
  refresh, sign-out/relogin, outage retry without upgrade modal, and forged success
  URL not showing a paid banner. All five navigation tabs and original Transcribe
  and Clip controls remained visible. These are simulated billing states, not proof
  of a real Stripe payment or an authenticated production test.
- The already-pinned Stripe SDK 9.12.0 was missing locally and installed for tests.
  No SDK API request, model download, or paid generation request was made.

## Files changed by this billing update

Backend: `app.py`, `stripe_billing.py`, `billing_state.py`, `billing_identity.py`,
`clipforge_studios/access.py`, `tests/test_verified_billing.py`,
`tests/test_studio_access.py`, `tests/test_studios_config.py`,
`tests/serve_studio_contract.py`, and this report.

Frontend: `src/Clipper.jsx`, `src/billingClient.mjs`, `tests/billing.test.mjs`.
Navigation, studio media/editor code, existing authentication modules, Supabase
configuration, billing prices, production environment files and deployment
configuration remain unchanged.

## Before any approved deployment

Review account evidence first. Verify production table shape and privileged
server write access; verify the existing webhook destination receives the handled
event types. Do not change secrets/prices or replay events without review. Backend
and frontend require a coordinated release because original clipping and billing
now require the existing login bearer token. Test the real paid account through
login, refresh, and clip generation afterward without creating another checkout.
Production entitlement cannot be declared repaired until those checks succeed.

Official references consulted:
- https://docs.stripe.com/billing/subscriptions/webhooks
- https://docs.stripe.com/webhooks
- https://docs.stripe.com/api/subscriptions/retrieve
- https://docs.stripe.com/api/checkout/sessions/retrieve
- https://docs.stripe.com/api/customers/list
- https://supabase.com/docs/reference/python/auth-getuser
