"""Billing identity must come from server-validated Supabase sessions."""
from fastapi import HTTPException
from clipforge_studios.access import auth_client


def billing_actor(request):
    header = request.headers.get('authorization', '')
    if not header.lower().startswith('bearer '):
        raise HTTPException(401, 'Sign in to verify subscription access')
    try:
        user = auth_client().auth.get_user(header.split(' ', 1)[1].strip()).user
        if not user or not user.id or not user.email or not user.email_confirmed_at:
            raise HTTPException(403, 'A verified email is required for billing access')
        return {'id':str(user.id), 'email':user.email.strip()}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, 'Session is invalid or expired') from None
