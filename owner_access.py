"""Explicit development entitlement, separate from paid subscription state.

Call only with an actor obtained from server session verification. No database
updates, email-based grants, customer quota changes, or provider spending bypass.
"""
import os
import uuid

def is_owner(actor):
    try:
        configured=os.getenv('CLIPFORGE_OWNER_USER_ID','').strip()
        return bool(configured and actor.get('email_verified') is True
                    and uuid.UUID(str(actor['id']))==uuid.UUID(configured))
    except (ValueError,KeyError,TypeError,AttributeError):return False

def development_access(actor):
    # dev_bypass is set only by dev_bypass.dev_actor for local loopback requests.
    if not (is_owner(actor) or (isinstance(actor,dict) and actor.get('dev_bypass') is True)):return None
    return dict(allowed=True,plan='owner_development',reason='owner_development',
                owner_development=True,unlimited=True,clips_remaining=None,
                payment_verified=False,upgrade=False)
