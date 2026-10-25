"""Offline Stripe/Supabase contracts. No API calls, charges or real credentials."""
import ast
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import asyncio
import tempfile
import unittest
from unittest.mock import Mock, AsyncMock, patch

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from billing_identity import billing_actor, checkout_reference
from billing_state import verified_state, VerificationError
import stripe_billing as billing


class StateTests(unittest.TestCase):
    def setUp(self):
        self.user=dict(email='Paid@Example.invalid',plan='expired',stripe_customer_id='cus_fixture')
        self.customer=dict(id='cus_fixture',email='paid@example.invalid')
        self.invoice=dict(id='in_fixture',paid=True,status='paid',customer='cus_fixture',subscription='sub_fixture')
        self.subscription=dict(id='sub_fixture',customer='cus_fixture',status='active',
            latest_invoice=self.invoice,items={'data':[{'price':{'id':'price_fixture'}}]})
        self.stripe=Mock()
        self.stripe.Customer.retrieve.return_value=self.customer
        self.stripe.Customer.list.return_value={'data':[self.customer]}
        self.stripe.Subscription.list.return_value={'data':[self.subscription]}
        self.stripe.Subscription.retrieve.side_effect=lambda *a,**kw:self.subscription
        self.db=Mock()
        self.db.table.return_value.update.return_value.eq.return_value.execute.return_value.data=[self.user]
        self.addCleanup(self.no_payments)

    def no_payments(self):
        self.stripe.Customer.create.assert_not_called()
        self.stripe.Subscription.create.assert_not_called()
        self.stripe.checkout.Session.create.assert_not_called()
        self.stripe.PaymentIntent.create.assert_not_called()

    def state(self, **kwargs):
        return verified_state(self.user,self.stripe,'price_fixture',**kwargs)

    def test_paid_active_and_cancelling_at_period_end_keep_access(self):
        self.subscription['cancel_at_period_end']=True
        state=self.state()
        self.assertEqual(state['plan'],'active');self.assertTrue(state['payment_verified'])

    def test_missing_mapping_recovered_from_matching_customer(self):
        self.user.pop('stripe_customer_id')
        self.assertEqual(self.state()['stripe_subscription_id'],'sub_fixture')

    def test_wrong_customer_email_never_grants_access(self):
        self.customer['email']='someone-else@example.invalid'
        with self.assertRaisesRegex(VerificationError,'mismatch'):self.state()

    def test_subscription_customer_must_match(self):
        self.subscription['customer']='cus_other'
        with self.assertRaises(VerificationError):self.state()

    def test_invoice_customer_and_subscription_must_match(self):
        for field,value in [('customer','cus_other'),('subscription','sub_other')]:
            with self.subTest(field=field):
                old=self.invoice[field];self.invoice[field]=value
                with self.assertRaises(VerificationError):self.state()
                self.invoice[field]=old

    def test_invoice_new_api_parent_subscription(self):
        self.invoice.pop('subscription')
        self.invoice['parent']={'subscription_details':{'subscription':'sub_fixture'}}
        self.assertTrue(self.state()['payment_verified'])

    def test_active_without_paid_invoice_is_not_paid(self):
        for invoice in (None,dict(self.invoice,paid=False,status='open')):
            self.subscription['latest_invoice']=invoice
            self.assertEqual(self.state()['plan'],'payment_pending')
            self.assertFalse(self.state()['payment_verified'])

    def test_unpaid_past_due_incomplete_paused_never_paid(self):
        for status in ('past_due','unpaid','incomplete','paused'):
            self.subscription['status']=status
            self.assertFalse(self.state()['payment_verified'])
            self.assertEqual(self.state()['plan'],'payment_pending')

    def test_cancelled_subscription_revokes_stale_paid_flag(self):
        self.user['plan']='active';self.subscription['status']='canceled'
        self.assertEqual(self.state()['plan'],'cancelled')

    def test_wrong_price_never_counts_as_clipforge_paid(self):
        self.subscription['items']['data'][0]['price']['id']='price_other'
        self.assertFalse(self.state()['payment_verified'])

    def test_trial_is_not_paid(self):
        self.subscription.update(status='trialing',trial_end=int(time.time())+3600)
        result=self.state();self.assertEqual(result['plan'],'trial');self.assertFalse(result['payment_verified'])

    def test_checkout_reference_cannot_cross_accounts(self):
        self.stripe.checkout.Session.retrieve.return_value=dict(mode='subscription',status='complete',customer='cus_fixture')
        self.assertTrue(self.state(checkout_id='cs_fixture')['payment_verified'])
        self.customer['email']='other@example.invalid'
        with self.assertRaises(VerificationError):self.state(checkout_id='cs_fixture')

    def test_open_checkout_not_treated_as_payment(self):
        self.stripe.checkout.Session.retrieve.return_value=dict(mode='subscription',status='open')
        with self.assertRaisesRegex(VerificationError,'pending'):self.state(checkout_id='cs_fixture')

    def test_persistence_and_repeat_login_reverify_stripe(self):
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_SECRET_KEY','fixture'),patch.object(billing,'STRIPE_PRICE_ID','price_fixture'):
            for _ in range(2):
                result=billing.reconcile_user(self.user,self.db)
                self.assertEqual(result['plan'],'active')
        self.assertEqual(self.stripe.Subscription.retrieve.call_count,2)
        self.db.table.return_value.update.assert_called_with(dict(plan='active',stripe_customer_id='cus_fixture',stripe_subscription_id='sub_fixture'))

    def test_expired_local_account_with_verified_payment_gets_paid_access_on_each_login(self):
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_SECRET_KEY','fixture'),patch.object(billing,'STRIPE_PRICE_ID','price_fixture'),patch.object(billing,'_db',return_value=self.db),patch.object(billing,'get_or_create_user',return_value=self.user):
            for _ in range(2):
                access=billing.check_clip_access(self.user['email'])
                self.assertTrue(access['allowed']);self.assertTrue(access['payment_verified'])
                self.assertEqual(access['plan'],'active');self.assertIsNone(access['clips_remaining'])

    def test_late_payment_failed_event_cannot_revoke_current_verified_payment(self):
        self.db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data=[self.user]
        self.stripe.Webhook.construct_event.return_value=dict(type='invoice.payment_failed',data={'object':{'customer':'cus_fixture'}})
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_SECRET_KEY','fixture'),patch.object(billing,'STRIPE_WEBHOOK_SECRET','fixture'),patch.object(billing,'STRIPE_PRICE_ID','price_fixture'),patch.object(billing,'_db',return_value=self.db):
            self.assertTrue(billing.handle_webhook(b'{}','signature')['ok'])
        self.db.table.return_value.update.assert_called_with(dict(plan='active',stripe_customer_id='cus_fixture',stripe_subscription_id='sub_fixture'))

    def test_webhook_hint_persists_missing_customer_link(self):
        self.user.pop('stripe_customer_id')
        self.stripe.Customer.list.return_value={'data':[]}
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_SECRET_KEY','fixture'),patch.object(billing,'STRIPE_PRICE_ID','price_fixture'):
            result=billing.reconcile_user(self.user,self.db,customer_hint='cus_fixture')
        self.assertEqual(result['stripe_customer_id'],'cus_fixture')
        self.db.table.return_value.update.assert_called_with(dict(plan='active',stripe_customer_id='cus_fixture',stripe_subscription_id='sub_fixture'))

    def test_failed_persistence_not_reported_as_success(self):
        self.db.table.return_value.update.return_value.eq.return_value.execute.return_value.data=[]
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_SECRET_KEY','fixture'),patch.object(billing,'STRIPE_PRICE_ID','price_fixture'):
            with self.assertRaisesRegex(VerificationError,'not_updated'):billing.reconcile_user(self.user,self.db)

    def test_api_failure_does_not_prompt_upgrade_or_allow(self):
        with patch.object(billing,'_db',return_value=self.db),patch.object(billing,'get_or_create_user',return_value=self.user),patch.object(billing,'reconcile_user',side_effect=VerificationError('billing_verification_unavailable')):
            access=billing.check_clip_access(self.user['email'])
        self.assertFalse(access['allowed']);self.assertFalse(access['upgrade'])

    def test_database_unavailable_fails_closed(self):
        with patch.object(billing,'_db',return_value=None):
            result=billing.check_clip_access(self.user['email'])
        self.assertFalse(result['allowed']);self.assertFalse(result['upgrade'])

    def test_paid_account_checkout_creates_nothing(self):
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'check_clip_access',return_value=dict(plan='active',payment_verified=True)):
            with self.assertRaisesRegex(VerificationError,'already_subscribed'):billing.create_checkout_session(self.user['email'])

    def test_pending_subscription_cannot_create_duplicate(self):
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'check_clip_access',return_value=dict(plan='payment_pending')):
            with self.assertRaises(VerificationError):billing.create_checkout_session(self.user['email'])

    def webhook(self,event):
        rows=self.db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value
        rows.data=[self.user]
        with patch.object(billing,'stripe',self.stripe),patch.object(billing,'STRIPE_WEBHOOK_SECRET','fixture'),patch.object(billing,'_db',return_value=self.db),patch.object(billing,'reconcile_user') as reconcile:
            self.stripe.Webhook.construct_event.return_value=event
            response=billing.handle_webhook(b'{}','signature')
            return response,reconcile

    def test_success_and_failure_webhooks_use_current_stripe_state(self):
        for kind in ('checkout.session.completed','invoice.paid','invoice.payment_succeeded','invoice.payment_failed','customer.subscription.created','customer.subscription.updated','customer.subscription.deleted'):
            with self.subTest(kind=kind):
                response,reconcile=self.webhook(dict(type=kind,data={'object':{'customer':'cus_fixture','status':'old-status'}}))
                self.assertTrue(response['ok']);reconcile.assert_called_once_with(self.user,self.db,customer_hint='cus_fixture')

    def test_unmatched_webhook_account_retries(self):
        self.customer['email']='someone-else@example.invalid'
        response,reconcile=self.webhook(dict(type='invoice.paid',data={'object':{'customer':'cus_fixture'}}))
        self.assertFalse(response['ok']);self.assertEqual(response['status'],503);reconcile.assert_not_called()

    def test_real_stripe_signature_validation(self):
        body=json.dumps(dict(id='evt_fixture',object='event',type='unused.event',data={'object':{}})).encode()
        timestamp=int(time.time());secret='fixture'
        signature=hmac.new(secret.encode(),str(timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
        with patch.object(billing,'STRIPE_WEBHOOK_SECRET',secret):
            self.assertTrue(billing.handle_webhook(body,f't={timestamp},v1={signature}')['ok'])
            self.assertFalse(billing.handle_webhook(body,f't={timestamp},v1=invalid')['ok'])


class IdentityTests(unittest.TestCase):
    def request(self, token=None):
        headers=[] if token is None else [(b'authorization',('Bearer '+token).encode())]
        return Request({'type':'http','headers':headers})

    def test_anonymous_or_forged_session_rejected(self):
        with self.assertRaises(HTTPException):billing_actor(self.request())
        with patch('billing_identity.auth_client') as client:
            client.return_value.auth.get_user.side_effect=ValueError('invalid')
            with self.assertRaises(HTTPException):billing_actor(self.request('forged'))

    def test_verified_account_is_server_identity(self):
        with patch('billing_identity.auth_client') as client:
            client.return_value.auth.get_user.return_value.user=SimpleNamespace(id='fixture-user',email='paid@example.invalid',email_confirmed_at='confirmed')
            self.assertEqual(billing_actor(self.request('fixture'))['email'],'paid@example.invalid')
            client.return_value.auth.get_user.assert_called_once_with('fixture')

    def test_unverified_email_rejected(self):
        with patch('billing_identity.auth_client') as client:
            client.return_value.auth.get_user.return_value.user=SimpleNamespace(id='fixture-user',email='paid@example.invalid',email_confirmed_at=None)
            with self.assertRaises(HTTPException):billing_actor(self.request('fixture'))

    def test_original_billing_routes_ignore_spoofed_email(self):
        source=Path(__file__).resolve().parents[1]/'app.py'
        nodes=ast.parse(source.read_text(encoding='utf-8')).body
        scope=dict(Request=Request,JSONResponse=JSONResponse,VerificationError=VerificationError,checkout_reference=checkout_reference,
            billing_actor=Mock(return_value={'email':'signed-in@example.invalid'}),
            check_clip_access=Mock(return_value={'allowed':True,'plan':'active','payment_verified':True}),
            create_checkout_session=Mock(return_value='https://checkout.example.invalid/fixture'))
        app=FastAPI()
        for name,path in [('billing_check_access','/billing/check-access'),('billing_checkout','/billing/checkout')]:
            node=next(n for n in nodes if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
            node.decorator_list=[]
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
            app.post(path)(scope[name])
        client=TestClient(app)
        self.assertEqual(client.post('/billing/check-access',data={'email':'victim@example.invalid'}).status_code,200)
        scope['check_clip_access'].assert_called_once_with('signed-in@example.invalid',checkout_id=None)
        self.assertEqual(client.post('/billing/checkout',data={'email':'victim@example.invalid'}).status_code,200)
        scope['create_checkout_session'].assert_called_once_with('signed-in@example.invalid')

    def manual_client(self, uploads, access):
        source=Path(__file__).resolve().parents[1]/'app.py'
        node=next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='clip_multi')
        node.decorator_list=[]
        scope=dict(Request=Request,UploadFile=UploadFile,File=File,Form=Form,JSONResponse=JSONResponse,
            os=os,json=json,asyncio=asyncio,UPLOAD_DIR=uploads,safe=lambda name:name,
            billing_actor=Mock(return_value={'email':'signed-in@example.invalid'}),
            check_clip_access=Mock(return_value=access),insert_transcript=Mock(return_value=None),
            record_clip_used=Mock(),seconds_to_text=lambda seconds:str(seconds),abs_url=lambda request,path:path,
            build_clip=AsyncMock(return_value=dict(duration_seconds=1,preview_path='preview.mp4',final_path=None,thumb_path=None)))
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        app=FastAPI();app.post('/clip_multi')(scope['clip_multi'])
        return TestClient(app),scope

    def test_original_manual_one_through_five_counts_and_identity_preserved(self):
        with tempfile.TemporaryDirectory() as uploads:
            client,scope=self.manual_client(uploads,dict(allowed=True,plan='active',payment_verified=True))
            for count in range(1,6):
                sections=json.dumps([dict(start='0',end='1') for _ in range(count)])
                response=client.post('/clip_multi',data={'sections':sections,'user_id':'victim@example.invalid'},files={'file':('fixture.mp4',b'offline render contract')})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(len(response.json()['items']),count)
                scope['check_clip_access'].assert_called_with('signed-in@example.invalid')
                scope['record_clip_used'].assert_called_with('signed-in@example.invalid')
                self.assertEqual(scope['insert_transcript'].call_args.kwargs['user_id'],'signed-in@example.invalid')

    def test_original_manual_billing_errors_do_not_offer_second_payment(self):
        with tempfile.TemporaryDirectory() as uploads:
            for plan,status in [('unknown',503),('payment_pending',409)]:
                client,scope=self.manual_client(uploads,dict(allowed=False,plan=plan))
                response=client.post('/clip_multi',data={'sections':'[]'})
                self.assertEqual(response.status_code,status)
                self.assertFalse(response.json()['upgrade']);scope['build_clip'].assert_not_called()

    def test_original_manual_cannot_skip_gate_as_anonymous(self):
        with tempfile.TemporaryDirectory() as uploads:
            client,scope=self.manual_client(uploads,dict(allowed=True,plan='active'))
            scope['billing_actor'].side_effect=HTTPException(401,'Unauthorized')
            self.assertEqual(client.post('/clip_multi',data={'sections':'[]','user_id':'anonymous'}).status_code,401)
            scope['check_clip_access'].assert_not_called();scope['build_clip'].assert_not_called()
