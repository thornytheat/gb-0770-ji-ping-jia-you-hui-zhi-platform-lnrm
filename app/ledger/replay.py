from datetime import datetime, timezone
import json
import sqlite3
from time import perf_counter

from app.configuration import RULESET,TEMPERATURE_COEFFICIENT
from app.diagnostics import RampError
from app.meters.catalog import fleet
from app.settlement.handover import reconcile_handover
from app.shift import compute
from .manifest import digest,lock,serialize


def replay(book, inputs):
    begin = perf_counter()
    catalog, reference = fleet()
    report = compute(inputs,catalog=catalog)
    setup = {'rules':RULESET,'input_sha256':{name:digest(data) for name,data in inputs.items()},
             'fleet_sha256':reference,'parameters':{'reference_temp_c':15,'thermal_coefficient':TEMPERATURE_COEFFICIENT,
                                                   'mass_unit':'g','shift':'one_bundle'}}
    batch = lock(setup)
    manifest = dict(setup,batch=batch,archived_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_ms=round((perf_counter()-begin)*1000),totals=report['balance']['totals'])
    with book.connection() as db:
        stored = db.execute('INSERT INTO shifts VALUES (?,?) ON CONFLICT(digest) DO NOTHING',(batch,serialize(manifest))).rowcount
        if stored:
            db.executemany('INSERT INTO stage_rows VALUES (?,?,?)',
                           [(batch,phase,serialize(values)) for phase,values in report.items()])
        saved = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()[0]
    return dict(json.loads(saved),reused=not bool(stored))


def list_shifts(book):
    with book.connection() as db:
        return [json.loads(item[0]) for item in db.execute('SELECT manifest FROM shifts ORDER BY rowid DESC LIMIT 25')]


def shift(book, batch):
    with book.connection() as db:
        manifest = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()
        if manifest is None:
            raise RampError('NO_BATCH',404)
        rows = db.execute('SELECT phase,body FROM stage_rows WHERE digest=?',(batch,)).fetchall()
    return {'manifest':json.loads(manifest[0]),'stages':{phase:json.loads(body) for phase,body in rows}}


def _load_snapshot(db, batch):
    manifest = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()
    if manifest is None:
        raise RampError('NO_BATCH',404)
    rows = db.execute('SELECT phase,body FROM stage_rows WHERE digest=?',(batch,)).fetchall()
    return {'manifest':json.loads(manifest[0]),'stages':{phase:json.loads(body) for phase,body in rows}}


def create_handover(book, previous_batch, incoming_batch):
    if previous_batch == incoming_batch:
        raise RampError('HANDOVER_SELF')
    with book.connection() as db:
        previous = _load_snapshot(db,previous_batch)
        incoming = _load_snapshot(db,incoming_batch)
        if db.execute('SELECT 1 FROM signatures WHERE digest=?',(incoming_batch,)).fetchone():
            raise RampError('EVIDENCE_LOCKED',409)
        result = reconcile_handover(previous,incoming)
        handover_digest = lock({'previous_batch':previous_batch,'incoming_batch':incoming_batch,
                                'trucks':result['trucks']})
        existing = db.execute('SELECT body FROM handovers WHERE digest=?',(handover_digest,)).fetchone()
        if existing:
            return dict(json.loads(existing[0]),reused=True)
        body = dict(result,digest=handover_digest,
                    archived_at=datetime.now(timezone.utc).isoformat())
        try:
            inserted = db.execute('INSERT INTO handovers VALUES (?,?,?,?)',
                                  (handover_digest,previous_batch,incoming_batch,serialize(body))).rowcount
        except sqlite3.IntegrityError as exc:
            raise RampError('HANDOVER_CONFLICT',409) from exc
        saved = db.execute('SELECT body FROM handovers WHERE digest=?',(handover_digest,)).fetchone()[0]
    return dict(json.loads(saved),reused=not bool(inserted))


def handover(book, handover_digest):
    with book.connection() as db:
        row = db.execute('SELECT body FROM handovers WHERE digest=?',(handover_digest,)).fetchone()
        if row is None:
            raise RampError('NO_HANDOVER',404)
        result = json.loads(row[0])
        previous = _load_snapshot(db,result['previous_batch'])
        incoming = _load_snapshot(db,result['incoming_batch'])
    return {'handover':result,
            'previous_snapshot':{'manifest':previous['manifest'],'sources':previous['stages']['intake']['sheets']},
            'incoming_snapshot':{'manifest':incoming['manifest'],'sources':incoming['stages']['intake']['sheets']}}


def handover_for_incoming(book, incoming_batch):
    with book.connection() as db:
        row = db.execute('SELECT digest FROM handovers WHERE incoming_batch=?',(incoming_batch,)).fetchone()
    if row is None:
        return None
    return handover(book,row[0])
