"""Read-only Stripe reconciliation. Never creates a customer, charge or subscription."""


class VerificationError(RuntimeError):
    pass


def normalized(email):
    return (email or '').strip().casefold()


def identifier(value):
    return value.get('id') if isinstance(value, dict) else value


def all_pages(resource, **params):
    page = resource.list(limit=100, **params)
    # Stripe ListObjects provide pagination; simple dictionaries support contract tests.
    return list(page.auto_paging_iter()) if hasattr(page, 'auto_paging_iter') else page['data']


def verified_state(user, stripe, price_id, checkout_id=None):
    """Return current entitlement after verifying account, product and invoice.

    Email comes exclusively from server-validated login. Stored billing identifiers
    are hints, never evidence of a successful payment. Trials are not paid plans.
    """
    if not price_id:
        raise VerificationError('billing_configuration_unavailable')
    email = normalized(user.get('email'))
    if not email:
        raise VerificationError('billing_account_mismatch')
    customers = []
    if user.get('stripe_customer_id'):
        customers.append(stripe.Customer.retrieve(user['stripe_customer_id']))
    if checkout_id:
        session = stripe.checkout.Session.retrieve(checkout_id)
        if session.get('mode') != 'subscription' or session.get('status') != 'complete':
            raise VerificationError('payment_pending')
        customer = stripe.Customer.retrieve(identifier(session.get('customer')))
        # Never trust the redirect parameter or session metadata as ownership proof.
        if normalized(customer.get('email')) != email:
            raise VerificationError('billing_account_mismatch')
        customers.append(customer)
    # Recover missing/outdated mappings, including multiple historical customers.
    # Stripe's email filter is case-sensitive; retain the verified original spelling.
    for spelling in dict.fromkeys([user['email'].strip(), email]):
        customers.extend(all_pages(stripe.Customer, email=spelling))
    seen = set(); candidates = []; existing = False
    for customer in customers:
        cid = customer.get('id')
        if cid in seen:
            continue
        seen.add(cid)
        if customer.get('deleted') or normalized(customer.get('email')) != email:
            raise VerificationError('billing_account_mismatch')
        for subscription in all_pages(stripe.Subscription, customer=cid, status='all'):
            # Retrieve current state, not a potentially old webhook/list snapshot.
            sub = stripe.Subscription.retrieve(subscription['id'], expand=['latest_invoice'])
            if identifier(sub.get('customer')) != cid:
                raise VerificationError('billing_account_mismatch')
            prices = [identifier(item.get('price')) for item in sub.get('items', {}).get('data', [])]
            if price_id not in prices:
                continue
            status = sub.get('status')
            if status not in ('canceled', 'incomplete_expired'):
                existing = True
            invoice = sub.get('latest_invoice')
            if isinstance(invoice, str):
                invoice = stripe.Invoice.retrieve(invoice)
            paid = bool(invoice and invoice.get('paid') is True and invoice.get('status') == 'paid')
            if invoice and identifier(invoice.get('customer')) != cid:
                raise VerificationError('billing_account_mismatch')
            # Older Stripe API versions put subscription directly on the invoice;
            # newer versions nest it under parent.subscription_details.
            invoice_sub = identifier((invoice or {}).get('subscription')) or identifier(
                (invoice or {}).get('parent', {}).get('subscription_details', {}).get('subscription'))
            if paid and invoice_sub != sub['id']:
                raise VerificationError('billing_account_mismatch')
            result = dict(stripe_customer_id=cid, stripe_subscription_id=sub['id'],
                          subscription_status=status, payment_verified=paid and status=='active',
                          existing_subscription=existing)
            if status == 'active' and paid:
                candidates.append((3, {**result, 'plan':'active'}))
            elif status == 'trialing' and sub.get('trial_end'):
                from datetime import datetime, timezone
                end = datetime.fromtimestamp(sub['trial_end'], timezone.utc)
                if end > datetime.now(timezone.utc):
                    candidates.append((2, {**result, 'plan':'trial', 'trial_end':end.isoformat()}))
            elif status in ('past_due','unpaid','incomplete','paused') or status=='active':
                candidates.append((1, {**result, 'plan':'payment_pending'}))
    if candidates:
        result = max(candidates, key=lambda pair:pair[0])[1]
        result['existing_subscription'] = existing
        return result
    if user.get('stripe_subscription_id') or user.get('plan') in ('active','payment_pending'):
        return dict(plan='cancelled', payment_verified=False, existing_subscription=existing)
    return dict(plan=user.get('plan','trial'), payment_verified=False, existing_subscription=existing)
