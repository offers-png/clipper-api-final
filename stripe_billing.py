# stripe_billing.py — ClipForge Stripe + usage gating
import os
import stripe
from datetime import datetime, timezone, date
from supabase import create_client

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
    res = db.table("clipforge_users").select("*").eq("email", email).limit(1).execute()
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

def check_clip_access(email: str) -> dict:
    """
    Returns {"allowed": bool, "reason": str, "plan": str,
             "clips_today": int, "clips_remaining": int | None}
    """
    db = _db()
    if not db:
        # If DB is down, allow (fail open) so we don't block users
        return {"allowed": True, "reason": "db_unavailable", "plan": "unknown",
                "clips_today": 0, "clips_remaining": None}

    user = get_or_create_user(email)
    if not user:
        return {"allowed": False, "reason": "user_not_found", "plan": "none",
                "clips_today": 0, "clips_remaining": 0}

    user = _reset_daily_if_needed(user, db)
    plan = user.get("plan", "trial")
    now  = datetime.now(timezone.utc)

    # ── Active paid subscriber → unlimited ───────────────────────────────────
    if plan == "active":
        return {"allowed": True, "reason": "paid", "plan": "active",
                "clips_today": user["clips_today"], "clips_remaining": None}

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
                    "clips_today": used, "clips_remaining": 0}
        return {"allowed": True, "reason": "trial_ok", "plan": "trial",
                "clips_today": used, "clips_remaining": remaining}

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
    user = get_or_create_user(email)
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
        subscription_data={
            "trial_period_days": TRIAL_DAYS,
        },
        success_url=f"{FRONTEND_URL}/clipper?upgrade=success",
        cancel_url=f"{FRONTEND_URL}/clipper?upgrade=cancelled",
        metadata={"email": email},
    )
    return session.url


# ── Stripe Webhook handler ────────────────────────────────────────────────────

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return {"ok": False, "error": "invalid_signature"}

    db = _db()
    if not db:
        return {"ok": False, "error": "db_unavailable"}

    etype = event["type"]
    data  = event["data"]["object"]

    if etype == "customer.subscription.created":
        _sync_subscription(data, db, "active")

    elif etype == "customer.subscription.updated":
        status = data.get("status")
        plan = "active" if status in ("active", "trialing") else "cancelled"
        _sync_subscription(data, db, plan)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        _sync_subscription(data, db, "cancelled")

    elif etype == "invoice.payment_failed":
        customer_id = data.get("customer")
        if customer_id:
            db.table("clipforge_users").update({"plan": "expired"}).eq(
                "stripe_customer_id", customer_id
            ).execute()

    return {"ok": True, "handled": etype}


def _sync_subscription(sub_data: dict, db, plan: str):
    customer_id = sub_data.get("customer")
    sub_id      = sub_data.get("id")
    if not customer_id:
        return
    db.table("clipforge_users").update({
        "plan": plan,
        "stripe_subscription_id": sub_id,
    }).eq("stripe_customer_id", customer_id).execute()
