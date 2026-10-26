"""Single-account, single-submission KIE trial. Never changes subscription access.

Quote verified at https://kie.ai/wan-2-7-video on 2026-10-26:
720p is 16 credits/second; 5 seconds = 80 credits = USD 0.40.
Reservations survive restarts and are never automatically refunded/retried.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import os
import re
import sqlite3
import time
import uuid
from .resource_paths import DATA

actor = ContextVar('kie_verified_actor', default=None)
ticket = ContextVar('kie_trial_ticket', default=None)
MODEL = 'wan/2-7-image-to-video'
COST_CENTS = 40
CREDITS = 80
LEDGER = DATA / 'kie-test-budget.sqlite3'

class BudgetError(RuntimeError): pass

def scoped():
    return bool(os.getenv('CLIPFORGE_KIE_TEST_USER_ID', '').strip())

def configuration():
    try:
        user = str(uuid.UUID(os.environ['CLIPFORGE_KIE_TEST_USER_ID']))
        approval = os.environ['CLIPFORGE_KIE_TEST_APPROVAL_ID']
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', approval): raise ValueError()
        budget = Decimal(os.environ['CLIPFORGE_KIE_TEST_BUDGET_USD'])
        if not budget.is_finite() or not Decimal('.40') <= budget <= Decimal('1'): raise ValueError()
        return user, approval
    except Exception:
        raise BudgetError('Account-only KIE test configuration is invalid; generation remains disabled') from None

@contextmanager
def database():
    db = sqlite3.connect(LEDGER, timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS attempts (approval TEXT PRIMARY KEY, user TEXT NOT NULL, state TEXT NOT NULL, reserved_cents INTEGER NOT NULL, credits INTEGER NOT NULL, task TEXT, created REAL NOT NULL)')
        db.commit()
        with db: yield db
    finally: db.close()

def check_actor():
    user, approval = configuration()
    current = actor.get()
    if not current or current.get('id') != user:
        raise BudgetError('KIE generation is restricted to the approved test account')
    return user, approval

def available():
    try:
        _, approval = check_actor()
        with database() as db:
            return db.execute('SELECT 1 FROM attempts WHERE approval=?', (approval,)).fetchone() is None
    except Exception: return False

def require_ticket():
    user, approval = check_actor()
    if ticket.get() != (user, approval):
        raise BudgetError('Only the approved single 5-second video test may call KIE')

@contextmanager
def attempt(request):
    user, approval = check_actor()
    if request.duration != 5:
        raise BudgetError('The approved KIE test must be exactly 5 seconds at 720p')
    # Write before uploads or network calls. Uncertain failures cannot reopen it.
    try:
        with database() as db:
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',
                       (approval,user,'reserved',COST_CENTS,CREDITS,None,time.time()))
    except sqlite3.IntegrityError:
        raise BudgetError('The single KIE test attempt has already been reserved; no retry is permitted') from None
    token = ticket.set((user, approval))
    try: yield
    finally: ticket.reset(token)

def claim_submission(model, inputs):
    require_ticket()
    if model != MODEL or inputs.get('duration') != 5 or inputs.get('resolution') != '720p' or inputs.get('prompt_extend') is not False:
        raise BudgetError('KIE request exceeds the approved model, duration or price limits')
    if set(inputs) != {'prompt','first_frame_url','duration','resolution','prompt_extend'}:
        raise BudgetError('Unapproved KIE input options are blocked')
    user, approval = ticket.get()
    with database() as db:
        changed = db.execute("UPDATE attempts SET state='submitted' WHERE approval=? AND user=? AND state='reserved'",
                             (approval,user)).rowcount
        if changed != 1:
            raise BudgetError('A paid KIE submission was already attempted; automatic retry is blocked')

def record_task(task):
    require_ticket()
    user, approval = ticket.get()
    with database() as db:
        db.execute('UPDATE attempts SET task=? WHERE approval=? AND user=?', (task,approval,user))
