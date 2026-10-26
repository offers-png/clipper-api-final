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
        return {'id':str(user.id), 'email':user.email.strip(), 'email_verified':True}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, 'Session is invalid or expired') from None


async def checkout_reference(request):
    """Accept legacy empty browser FormData without invoking the old parser bug."""
    body=await request.body()
    if len(body)>16384:
        raise HTTPException(413,'Billing verification request is too large')
    if not body:
        return None
    try:
        from python_multipart.multipart import parse_options_header
    except ImportError:  # Existing production dependency: python-multipart 0.0.9.
        from multipart.multipart import parse_options_header
    content_type, options=parse_options_header(request.headers.get('content-type',''))
    boundary=options.get(b'boundary')
    if content_type==b'multipart/form-data' and boundary and body.strip()==b'--'+boundary+b'--':
        return None
    try:
        form=await request.form()
    except Exception:
        raise HTTPException(400,'Invalid billing verification form') from None
    return form.get('session_id') or None
