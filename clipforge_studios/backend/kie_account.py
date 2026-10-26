"""Account-only KIE quotes and durable spending reservations. No billing changes.

Verified 2026-10-26 at kie.ai/nano-banana and kie.ai/wan-2-7-video:
Nano Banana Edit = 4 credits/image; Wan 2.7 720p = 16 credits/second.
One credit is approximately USD .005. No frontend price is trusted.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from fastapi import HTTPException
from . import kie_budget as legacy
from .resource_paths import DATA

LEDGER=DATA/'kie-account-spending.sqlite3'
permit=ContextVar('kie_account_permit',default=None)
IMAGE_MODEL='google/nano-banana-edit'
VIDEO_MODEL='wan/2-7-image-to-video'

def active():return os.getenv('CLIPFORGE_KIE_ACCOUNT_MODE')=='1'

def user():
    actor=legacy.actor.get()
    try:
        allowed={str(uuid.UUID(x.strip())) for x in os.getenv('CLIPFORGE_KIE_ALLOWED_USER_IDS','').split(',') if x.strip()}
        if not actor or actor['id'] not in allowed:raise ValueError()
        return actor['id']
    except (ValueError,KeyError,TypeError):raise HTTPException(403,'KIE generation is not enabled for this account') from None

def limit():
    try:
        amount=Decimal(os.getenv('CLIPFORGE_KIE_SPENDING_LIMIT_USD','0'))
        if not amount.is_finite() or not 0<amount<=10000:raise ValueError()
        credits=int(amount/Decimal('.005'))
        if credits<1:raise ValueError()
        return credits
    except (ValueError,InvalidOperation):raise HTTPException(503,'The account spending limit needs configuration') from None

@contextmanager
def database():
    db=sqlite3.connect(LEDGER,timeout=30);db.row_factory=sqlite3.Row
    try:
        db.execute('CREATE TABLE IF NOT EXISTS quotes (id TEXT PRIMARY KEY,user TEXT NOT NULL,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,credits INTEGER NOT NULL,held INTEGER NOT NULL,tasks INTEGER NOT NULL,duration INTEGER NOT NULL,state TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS submissions (quote TEXT NOT NULL,ordinal INTEGER NOT NULL,task TEXT,state TEXT NOT NULL,PRIMARY KEY(quote,ordinal))')
        db.commit()
        with db:yield db
    finally:db.close()

def historical(uid):
    # Preserve the previous one-shot ledger and count its submitted exposure.
    if not legacy.LEDGER.is_file():return 0
    db=sqlite3.connect(legacy.LEDGER)
    try:return int(db.execute("SELECT COALESCE(sum(credits),0) FROM attempts WHERE user=? AND state='submitted'",(uid,)).fetchone()[0])
    finally:db.close()

def held(db,uid):return historical(uid)+int(db.execute('SELECT COALESCE(sum(held),0) FROM quotes WHERE user=?',(uid,)).fetchone()[0])
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def usd(credits):return str((Decimal(credits)*Decimal('.005')).quantize(Decimal('.001')))

def status(configured):
    try:
        uid=user();cap=limit()
        with database() as db:used=held(db,uid)
        return dict(available=bool(configured),enabled=True,confirmation_required=True,quote_required=True,
            spending_limit_usd=usd(cap),committed_usd=usd(used),remaining_usd=usd(max(0,cap-used)),
            reason='Generation is enabled for your account. Review the credit cost before generating.' if configured else 'KIE server connection is not configured.')
    except HTTPException as error:return dict(available=False,enabled=False,confirmation_required=True,quote_required=True,reason=error.detail)

def quote(kind,binding,balance):
    uid=user();cap=limit()
    if kind=='images':credits,tasks,duration=12,3,0
    elif kind=='video':
        duration=binding.get('duration')
        if type(duration)!=int or not 2<=duration<=15:raise HTTPException(422,'Choose a video duration from 2 to 15 seconds')
        credits,tasks=16*duration,1
    else:raise HTTPException(422,'Unsupported generation type')
    with database() as db:
        used=held(db,uid)
        pending=db.execute("SELECT 1 FROM quotes WHERE user=? AND state IN ('reserved','uncertain')",(uid,)).fetchone()
        reason='An earlier paid request is still pending or uncertain. Check it before starting another.' if pending else 'The account spending limit would be exceeded.' if used+credits>cap else 'KIE has insufficient credits. Add credits in your KIE account before generating.' if balance<credits else None
        qid=uuid.uuid4().hex
        db.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)',(qid,uid,kind,fingerprint(binding),credits,0,tasks,duration,'quoted',time.time()+600,time.time()))
    return dict(quote_id=qid,kind=kind,count=tasks,model=IMAGE_MODEL if kind=='images' else VIDEO_MODEL,
        cost_credits=credits,cost_usd=usd(credits),balance_credits=str(balance),spending_limit_usd=usd(cap),
        committed_usd=usd(used),remaining_usd=usd(max(0,cap-used)),can_submit=reason is None,
        reason=reason or 'Confirm to upload reference media to KIE and spend these credits.',expires_in_seconds=600)

def reserve(qid,kind,binding,balance,confirmed):
    uid=user();cap=limit()
    if not confirmed or not qid:raise HTTPException(428,'Review the credit quote and confirm spending first')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        q=db.execute('SELECT * FROM quotes WHERE id=? AND user=?',(qid,uid)).fetchone()
        if q is None or q['kind']!=kind or q['fingerprint']!=fingerprint(binding):raise HTTPException(409,'The quote does not match this request; request a new quote')
        if q['state']!='quoted':raise HTTPException(409,'This quote has already been used; no duplicate submission was made')
        if q['expires']<time.time():raise HTTPException(409,'The quote expired; request a new quote')
        if db.execute("SELECT 1 FROM quotes WHERE user=? AND state IN ('reserved','uncertain')",(uid,)).fetchone():raise HTTPException(409,'A paid request is pending or uncertain; no additional job was submitted')
        if held(db,uid)+q['credits']>cap:raise HTTPException(403,'The account spending limit would be exceeded')
        if balance<q['credits']:raise HTTPException(409,'KIE has insufficient credits; no paid request was submitted')
        db.execute("UPDATE quotes SET state='reserved',held=credits WHERE id=?",(qid,))
    return qid

@contextmanager
def execution(qid):
    uid=user()
    with database() as db:q=db.execute("SELECT * FROM quotes WHERE id=? AND user=? AND state='reserved'",(qid,uid)).fetchone()
    if not q:raise RuntimeError('No valid account spending reservation')
    token=permit.set(dict(q))
    try:yield
    finally:permit.reset(token)

def require_execution():
    uid=user();q=permit.get()
    if not q or q['user']!=uid:raise RuntimeError('KIE requires a confirmed account spending reservation')
    return q

def claim_submission(model,inputs,balance):
    q=require_execution()
    if q['kind']=='images':
        valid=model==IMAGE_MODEL and set(inputs)=={'prompt','image_urls','aspect_ratio','output_format'} and inputs.get('output_format')=='png' and inputs.get('aspect_ratio') in ('16:9','9:16','1:1') and len(inputs.get('image_urls',[]))==1
        unit=4
    else:
        valid=model==VIDEO_MODEL and set(inputs)=={'prompt','first_frame_url','duration','resolution','prompt_extend'} and inputs.get('duration')==q['duration'] and inputs.get('resolution')=='720p' and inputs.get('prompt_extend') is False
        unit=16*q['duration']
    if not valid:raise RuntimeError('KIE request differs from its server-priced quote')
    if balance<unit:raise RuntimeError('KIE balance is insufficient; this paid task was not submitted')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT state FROM quotes WHERE id=?',(q['id'],)).fetchone()
        n=db.execute('SELECT count(*) FROM submissions WHERE quote=?',(q['id'],)).fetchone()[0]
        if current['state']!='reserved' or n>=q['tasks']:raise RuntimeError('Paid submission limit reached; no automatic retry was attempted')
        db.execute('INSERT INTO submissions VALUES (?,?,?,?)',(q['id'],n+1,None,'submitted'))

def record_task(task):
    q=require_execution()
    with database() as db:db.execute('UPDATE submissions SET task=? WHERE quote=? AND ordinal=(SELECT max(ordinal) FROM submissions WHERE quote=?)',(task,q['id'],q['id']))

def terminal(state):
    q=require_execution()
    with database() as db:db.execute('UPDATE submissions SET state=? WHERE quote=? AND ordinal=(SELECT max(ordinal) FROM submissions WHERE quote=?)',(state,q['id'],q['id']))

def finish(qid,success):
    uid=user()
    with database() as db:
        q=db.execute('SELECT * FROM quotes WHERE id=? AND user=?',(qid,uid)).fetchone()
        if not q or q['state']!='reserved':return
        rows=db.execute('SELECT state FROM submissions WHERE quote=?',(qid,)).fetchall()
        uncertain=any(r['state']=='submitted' for r in rows)
        attempted=sum(r['state']!='rejected' for r in rows)
        db.execute('UPDATE quotes SET state=?,held=? WHERE id=?',('uncertain' if uncertain else 'completed' if success else 'failed',attempted*(q['credits']//q['tasks']),qid))
