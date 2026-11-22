from flask import Blueprint, current_app, jsonify, request
from app.bundles import submitted
from app.diagnostics import RampError
from app.dispatch.signatures import evidence_file,sign
from app.ledger.replay import create_handover,handover,list_shifts,replay,shift
from app.shift import compute

routes = Blueprint('ramp_api',__name__)


def evaluate(stage):
    return compute(submitted(),stage)[stage]


@routes.route('/api/intake',methods=['GET','POST'])
def intake():
    return evaluate('intake')


@routes.route('/api/versions',methods=['GET','POST'])
def versions():
    return evaluate('versions')


@routes.route('/api/readings',methods=['GET','POST'])
def readings():
    return evaluate('readings')


@routes.route('/api/mass',methods=['GET','POST'])
def mass():
    return evaluate('mass')


@routes.route('/api/balance',methods=['GET','POST'])
def balance():
    return evaluate('balance')


@routes.get('/api/shifts')
def shifts():
    return {'shifts':list_shifts(current_app.book)}


@routes.post('/api/shifts')
def archive():
    result = replay(current_app.book,submitted())
    return result,200 if result['reused'] else 201


@routes.get('/api/shifts/<batch>')
def load(batch):
    return shift(current_app.book,batch)


@routes.post('/api/shifts/<batch>/sign')
def issue(batch):
    return sign(current_app.book,batch)


@routes.get('/api/shifts/<batch>/export')
def export(batch):
    result = jsonify(evidence_file(current_app.book,batch))
    result.headers['Content-Disposition'] = f'attachment; filename="fuel-{batch}.json"'
    return result


@routes.post('/api/handovers')
def handoff():
    body = request.get_json(silent=True)
    if not (isinstance(body,dict) and isinstance(body.get('previous_batch'),str)
            and isinstance(body.get('incoming_batch'),str) and len(body)==2):
        raise RampError('HANDOVER_BODY')
    result = create_handover(current_app.book,body['previous_batch'],body['incoming_batch'])
    return result,200 if result['reused'] else 201


@routes.get('/api/handovers/<handover_digest>')
def handoff_detail(handover_digest):
    return handover(current_app.book,handover_digest)
