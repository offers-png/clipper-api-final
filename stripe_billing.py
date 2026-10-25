# stripe_billing.py — ClipForge Stripe + usage gating
import os
import stripe
from datetime import datetime, timezone, date
from supabase import create_client
from billing_state import verified_state, VerificationError, normalized

STRIPE_SECRET_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID        = os.getenv("STRIPE_PRICE_ID", "")   # $9.99/mo price ID
FRONTEND_URL           = os.getenv("FRONTEND_URL", "https://clipper-frontend.onrender.com")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")

FREE_CLIPS_PER_DAY = 3   # during trial
TRIAL_DAYS = 7

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

def _db():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── User record helpers ───────────────────────────────────────────────────────

def get_or_create_user(email: str) -> dict:
    """Return the clipforge_users row for this email, creating it if needed."""
    db = _db()
    if not db:
        return {}
    email = email.strip()
    # Case-insensitive exact matching; escape LIKE metacharacters in email addresses.
    pattern = email.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    res = db.table("clipforge_users").select("*").ilike("email", pattern).limit(2).execute()
    if len(res.data or []) > 1:
        raise VerificationError('billing_account_ambiguous')
    if res.data:
        return res.data[0]
    # Create fresh trial user
    insert = db.table("clipforge_users").insert({"email": email}).execute()
    return insert.data[0] if insert.data else {}


def _reset_daily_if_needed(user: dict, db) -> dict:
    """Reset clips_today if it's a new calendar day."""
    today = date.today().isoformat()
    if user.get("clips_today_date") != today:
        db.table("clipforge_users").update({
            "clips_today": 0,
            "clips_today_date": today,
        }).eq("email", user["email"]).execute()
        user["clips_today"] = 0
        user["clips_today_date"] = today
    return user


# ── Access check ──────────────────────────────────────────────────────────────

def reconcile_user(user, db, checkout_id=None, customer_hint=None):
    if not STRIPE_SECRET_KEY:
        raise VerificationError('billing_configuration_unavailable')
    try:
        lookup = {**user, 'stripe_customer_id':customer_hint} if customer_hint else user
        state = verified_state(lookup, stripe, STRIPE_PRICE_ID, checkout_id)
    except VerificationError:
        raise
    except Exception:
        raise VerificationError('billing_verification_unavailable') from None
    fields = {key:value for key,value in state.items()
              if key in ('plan','stripe_customer_id','stripe_subscription_id','trial_end')}
    if any(user.get(key) != value for key,value in fields.items()):
        result = db.table('clipforge_users').update(fields).eq('email', user['email']).execute()
        if not result.data:
            raise VerificationError('billing_record_not_updated')
    return {**user, **state}


def check_clip_access(email: str, checkout_id=None) -> dict:
    """Re-check Stripe on each authenticated access; errors never grant access."""
    try:
        db = _db()
        if not db:
            raise VerificationError('db_unavailable')
        user = get_or_create_user(email)
        if not user:
            raise VerificationError('user_not_found')
        user = reconcile_user(user, db, checkout_id)
    except Exception as error:
        reason = str(error) if isinstance(error, VerificationError) else 'billing_verification_unavailable'
        return {'allowed':False, 'reason':reason, 'plan':'unknown', 'upgrade':False,
                'clips_today':0, 'clips_remaining':0, 'payment_verified':False}

    email = user['email']
    plan = user.get("plan", "trial")
    now  = datetime.now(timezone.utc)

    # ── Active paid subscriber → unlimited ───────────────────────────────────
    if plan == "active":
        return {"allowed": True, "reason": "paid", "plan": "active",
                "clips_today": user.get("clips_today", 0), "clips_remaining": None,
                "payment_verified": True, "upgrade": False}

    try:
        user = _reset_daily_if_needed(user, db)
    except Exception:
        return {'allowed':False, 'reason':'billing_verification_unavailable', 'plan':'unknown',
                'upgrade':False, 'clips_today':0, 'clips_remaining':0, 'payment_verified':False}

    # ── Trial ────────────────────────────────────────────────────────────────
    if plan == "trial":
        trial_end = user.get("trial_end")
        if isinstance(trial_end, str):
            trial_end = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))
        if trial_end and now > trial_end:
            # Trial expired — mark expired
            db.table("clipforge_users").update({"plan": "expired"}).eq("email", email).execute()
            return {"allowed": False, "reason": "trial_expired", "plan": "expired",
                    "clips_today": user["clips_today"], "clips_remaining": 0}

        used  = user.get("clips_today", 0)
        remaining = max(0, FREE_CLIPS_PER_DAY - used)
        if used >= FREE_CLIPS_PER_DAY:
            return {"allowed": False, "reason": "daily_limit", "plan": "trial",
                    "clips_today": used, "clips_remaining": 0,
                    "existing_subscription": bool(user.get('existing_subscription')),
                    "subscription_status": user.get('subscription_status'), "payment_verified": False}
        return {"allowed": True, "reason": "trial_ok", "plan": "trial",
                "clips_today": used, "clips_remaining": remaining,
                "existing_subscription": bool(user.get('existing_subscription')),
                "subscription_status": user.get('subscription_status'), "payment_verified": False}

    # ── Expired / Cancelled ───────────────────────────────────────────────────
    return {"allowed": False, "reason": plan, "plan": plan,
            "clips_today": user.get("clips_today", 0), "clips_remaining": 0}


def record_clip_used(email: str):
    """Increment clips_today and total_clips after a successful clip."""
    db = _db()
    if not db:
        return
    try:
        user = get_or_create_user(email)
        today = date.today().isoformat()
        clips_today = user.get("clips_today", 0) if user.get("clips_today_date") == today else 0
        db.table("clipforge_users").update({
            "clips_today": clips_today + 1,
            "clips_today_date": today,
            "total_clips": (user.get("total_clips") or 0) + 1,
        }).eq("email", email).execute()
    except Exception as e:
        print(f"⚠️ record_clip_used failed: {e}")


# ── Stripe Checkout ───────────────────────────────────────────────────────────

def create_checkout_session(email: str) -> str:
    """Create a Stripe Checkout session with 7-day trial. Returns the URL."""
    access = check_clip_access(email)
    if access.get('plan') == 'active' and access.get('payment_verified'):
        raise VerificationError('already_subscribed')
    if access.get('plan') in ('unknown', 'payment_pending'):
        raise VerificationError('billing_verification_required')
    user = reconcile_user(get_or_create_user(email), _db())
    if user.get('existing_subscription'):
        raise VerificationError('existing_subscription_requires_review')
    customer_id = user.get("stripe_customer_id")

    # Reuse existing Stripe customer or create new
    if not customer_id:
        customer = stripe.Customer.create(email=email)
        customer_id = customer.id
        db = _db()
        if db:
            db.table("clipforge_users").update(
                {"stripe_customer_id": customer_id}
            ).eq("email", email).execute()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        mode="subscription",

        allow_promotion_codes=True,
        
        subscription_data={
            "trial_period_days": TRIAL_DAYS,
        },
        success_url=f"{FRONTEND_URL}/clipper?upgrade=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{FRONTEND_URL}/clipper?upgrade=cancelled",
        metadata={"email": email},
    )
    return session.url


# ── Stripe Webhook handler ────────────────────────────────────────────────────

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    if not STRIPE_WEBHOOK_SECRET or not sig_header:
        return {'ok':False, 'error':'invalid_signature', 'status':400}
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (stripe.error.SignatureVerificationError, ValueError):
        return {'ok':False, 'error':'invalid_signature', 'status':400}
    etype = event['type']
    supported = {'checkout.session.completed', 'checkout.session.async_payment_succeeded',
                 'invoice.paid', 'invoice.payment_succeeded', 'invoice.payment_failed',
                 'customer.subscription.created', 'customer.subscription.updated',
                 'customer.subscription.deleted', 'customer.subscription.paused',
                 'customer.subscription.resumed'}
    if etype not in supported:
        return {'ok':True, 'handled':'ignored'}
    try:
        db = _db()
        if not db:
            raise VerificationError('db_unavailable')
        obj = event['data']['object']
        cid = obj.get('customer')
        if not cid:
            raise VerificationError('billing_customer_missing')
        from billing_state import identifier
        cid = identifier(cid)
        customer = stripe.Customer.retrieve(cid)
        if customer.get('deleted') or not customer.get('email'):
            raise VerificationError('billing_account_mismatch')
        rows = db.table('clipforge_users').select('*').eq('stripe_customer_id',cid).limit(2).execute().data or []
        if not rows:
            # A verified event may arrive before the checkout mapping was saved.
            # Match an existing account; never invent a user from webhook metadata.
            pattern = customer['email'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            rows = db.table('clipforge_users').select('*').ilike('email',pattern).limit(2).execute().data or []
        if len(rows)!=1 or normalized(rows[0]['email'])!=normalized(customer['email']):
            raise VerificationError('billing_account_not_linked')
        # Retrieve CURRENT Stripe state for duplicate/out-of-order deliveries.
        # Do not apply stale event payloads or mark subscription.created as paid.
        reconcile_user(rows[0], db, customer_hint=cid)
        return {'ok':True, 'handled':etype}
    except Exception:
        # Non-2xx asks Stripe to retry; do not acknowledge a failed/zero-row update.
        return {'ok':False, 'error':'billing_sync_failed', 'status':503}
