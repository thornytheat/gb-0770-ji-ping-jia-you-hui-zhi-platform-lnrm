from app.diagnostics import RampError
from app.ledger.manifest import lock
from app.ledger.replay import handover_for_incoming, shift
from .document import compile_slip


def _slip(book, batch):
    snapshot = shift(book,batch)
    handover = handover_for_incoming(book,batch)
    return compile_slip(snapshot,handover)


def sign(book, batch):
    evidence = _slip(book,batch)
    checksum = lock(evidence)
    with book.connection() as db:
        inserted = db.execute('INSERT INTO signatures VALUES (?,?) ON CONFLICT(digest) DO NOTHING',(batch,checksum)).rowcount
        saved = db.execute('SELECT checksum FROM signatures WHERE digest=?',(batch,)).fetchone()[0]
    return {'batch':batch,'checksum':saved,'reused':not bool(inserted),
            'open_items':sum(n for key,n in evidence['conservation']['states'].items() if key!='MATCH') +
                         len(evidence['conservation']['unmatched_receipts']),
            'held_trucks':evidence['conservation']['held_trucks']}


def evidence_file(book, batch):
    with book.connection() as db:
        signature = db.execute('SELECT checksum FROM signatures WHERE digest=?',(batch,)).fetchone()
    if signature is None:
        raise RampError('NOT_SIGNED',409)
    document = _slip(book,batch)
    return {'checksum':signature[0],'verified':signature[0]==lock(document),'slip':document}
