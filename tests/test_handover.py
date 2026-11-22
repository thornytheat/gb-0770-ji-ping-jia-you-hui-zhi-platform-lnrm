import pytest

from app.ledger.replay import create_handover, handover, replay
from app.main import create_app

TICKET_HEADER = 'ticket,truck,opened,closed,start_l,stop_l,event,fuel,rho,rho_unit,temp_c'
RECEIPT_HEADER = 'ticket,truck,signed_kg,document'
STOCK_HEADER = 'truck,movement,kind,kg'


def ticket_row(ticket, truck, stop_l):
    return (f'{ticket},{truck},2026-11-22T04:00:00+00:00,2026-11-22T04:10:00+00:00,'
            f'100,{stop_l},normal,JET-A1,800,kg/m3,15')


def receipt_row(ticket, truck, signed_kg):
    return f'{ticket},{truck},{signed_kg},DOC-{ticket}'


def bundle(stock_rows, tickets, receipts):
    return {
        'tickets': (TICKET_HEADER + '\n' + '\n'.join(ticket_row(*item) for item in tickets) + '\n').encode(),
        'receipts': (RECEIPT_HEADER + '\n' + '\n'.join(receipt_row(*item) for item in receipts) + '\n').encode(),
        'stock': (STOCK_HEADER + '\n' + '\n'.join(stock_rows) + '\n').encode(),
    }


def previous_bundle():
    # 上班签单期末：FT-12=157kg FT-18=104kg FT-23=80kg
    stock = ['FT-12,O1,opening,300', 'FT-12,L1,inbound,20', 'FT-12,C1,closing,157',
             'FT-18,O2,opening,140', 'FT-18,C2,closing,104',
             'FT-23,O3,opening,90', 'FT-23,C3,closing,80']
    return bundle(stock, [('PA', 'FT-23', '105')], [('PA', 'FT-23', '4')])


def incoming_bundle():
    # 接班复秤期初：FT-12=152kg（短少5kg）FT-18=106kg（溢2kg）FT-23=80kg（吻合）
    stock = ['FT-12,O4,opening,152', 'FT-12,C4,closing,150',
             'FT-18,O5,opening,106', 'FT-18,C5,closing,100',
             'FT-23,O6,opening,80', 'FT-23,C6,closing,75']
    return bundle(stock, [('IA', 'FT-23', '105')], [('IA', 'FT-23', '4')])


@pytest.fixture
def two_shifts(ramp):
    previous = replay(ramp.book, previous_bundle())
    incoming = replay(ramp.book, incoming_bundle())
    return previous['batch'], incoming['batch']


def test_handover_difference_booked_once_and_is_idempotent(ramp, two_shifts):
    previous_batch, incoming_batch = two_shifts
    first = create_handover(ramp.book, previous_batch, incoming_batch)
    assert first['reused'] is False
    again = create_handover(ramp.book, previous_batch, incoming_batch)
    assert again['reused'] is True and again['digest'] == first['digest']
    with ramp.book.connection() as db:
        assert db.execute('SELECT count(*) FROM handovers').fetchone()[0] == 1
    by_truck = {row['truck']: row for row in first['trucks']}
    assert by_truck['FT-12']['difference_g'] == -5000 and by_truck['FT-12']['state'] == 'HANDOVER_SHORT'
    assert by_truck['FT-18']['difference_g'] == 2000 and by_truck['FT-18']['state'] == 'HANDOVER_OVER'
    assert by_truck['FT-23']['difference_g'] == 0 and by_truck['FT-23']['state'] == 'HANDOVER_MATCH'
    assert (first['total_difference_g'], first['short_g'], first['over_g']) == (-3000, -5000, 2000)
    assert (first['short_count'], first['over_count'], first['held_trucks']) == (1, 1, 0)


def test_incoming_balance_keeps_reweighed_anchor_and_is_not_touched(ramp, two_shifts):
    previous_batch, incoming_batch = two_shifts
    before = ramp.book
    from app.ledger.replay import shift
    balance_before = shift(ramp.book, incoming_batch)['stages']['balance']
    create_handover(ramp.book, previous_batch, incoming_batch)
    balance_after = shift(ramp.book, incoming_batch)['stages']['balance']
    assert balance_before == balance_after
    ft12 = next(row for row in balance_after['stocks'] if row['truck'] == 'FT-12')
    assert ft12['opening_g'] == 152000 and ft12['difference_g'] == 2000


def test_handover_route_retry_does_not_duplicate(client, two_shifts):
    previous_batch, incoming_batch = two_shifts
    payload = {'previous_batch': previous_batch, 'incoming_batch': incoming_batch}
    first = client.post('/api/handovers', json=payload)
    second = client.post('/api/handovers', json=payload)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json == dict(first.json, reused=True)


def test_signed_incoming_cannot_accept_handover(client, two_shifts):
    previous_batch, incoming_batch = two_shifts
    assert client.post(f'/api/shifts/{incoming_batch}/sign', json={}).status_code == 200
    response = client.post('/api/handovers',
                           json={'previous_batch': previous_batch, 'incoming_batch': incoming_batch})
    assert (response.status_code, response.json['error']) == (409, 'EVIDENCE_LOCKED')


def test_signed_evidence_carries_handover_and_both_original_sheets(client, two_shifts):
    previous_batch, incoming_batch = two_shifts
    mark = client.post('/api/handovers',
                       json={'previous_batch': previous_batch, 'incoming_batch': incoming_batch}).json
    sign = client.post(f'/api/shifts/{incoming_batch}/sign', json={}).json
    export = client.get(f'/api/shifts/{incoming_batch}/export').json
    assert export['verified'] and sign['checksum']
    slip = export['slip']
    assert slip['handover']['digest'] == mark['digest']
    assert slip['handover']['total_difference_g'] == -3000
    assert slip['previous_sources']['stock']['rows'][0]['data']['truck'] == 'FT-12'
    assert slip['sources']['stock']['rows'][0]['data']['truck'] == 'FT-12'
    detail = client.get('/api/handovers/' + mark['digest']).json
    assert detail['handover']['digest'] == mark['digest']
    assert detail['previous_snapshot']['manifest']['batch'] == previous_batch
    assert detail['incoming_snapshot']['manifest']['batch'] == incoming_batch
    assert detail['previous_snapshot']['sources']['stock']['accepted'] == 7
    assert detail['incoming_snapshot']['sources']['stock']['accepted'] == 6


def test_shift_without_handover_exports_unchanged_shape(client):
    batch = client.post('/api/shifts', json={'sample': 'dirty'}).json['batch']
    client.post(f'/api/shifts/{batch}/sign', json={})
    slip = client.get(f'/api/shifts/{batch}/export').json['slip']
    assert 'handover' not in slip and 'previous_sources' not in slip
    assert set(slip) == {'document', 'manifest', 'conservation', 'counter_checks', 'sources'}


def test_self_unknown_and_malformed_handover_requests(client, two_shifts):
    previous_batch, incoming_batch = two_shifts
    self_resp = client.post('/api/handovers',
                            json={'previous_batch': incoming_batch, 'incoming_batch': incoming_batch})
    assert (self_resp.status_code, self_resp.json['error']) == (422, 'HANDOVER_SELF')
    missing = client.post('/api/handovers',
                          json={'previous_batch': 'nope', 'incoming_batch': incoming_batch})
    assert (missing.status_code, missing.json['error']) == (404, 'NO_BATCH')
    bad_body = client.post('/api/handovers', json={'incoming_batch': incoming_batch})
    assert (bad_body.status_code, bad_body.json['error']) == (422, 'HANDOVER_BODY')
    assert client.get('/api/handovers/missing').status_code == 404


def test_one_incoming_shift_can_only_link_one_previous(client, two_shifts):
    previous_batch, incoming_batch = two_shifts
    # 制造一个不同的上班批次
    changed = previous_bundle()
    changed['stock'] = changed['stock'].replace(b'FT-12,C1,closing,157', b'FT-12,C1,closing,156')
    other_batch = replay(client.application.book, changed)['batch']
    assert other_batch != previous_batch
    assert client.post('/api/handovers',
                       json={'previous_batch': previous_batch,
                             'incoming_batch': incoming_batch}).status_code == 201
    conflict = client.post('/api/handovers',
                           json={'previous_batch': other_batch, 'incoming_batch': incoming_batch})
    assert (conflict.status_code, conflict.json['error']) == (409, 'HANDOVER_CONFLICT')


def test_missing_anchor_truck_is_held_and_excluded_from_totals(ramp, two_shifts):
    previous_batch, incoming_batch = two_shifts
    held_stock = (STOCK_HEADER + '\n'
                  'FT-99,O9,opening,50\nFT-99,C9,closing,40\n').encode()
    held_incoming = incoming_bundle()
    held_incoming['stock'] = held_incoming['stock'].replace(b'\n', b'\nFT-99,O7,opening,38\n', 1)
    incoming = replay(ramp.book, held_incoming)['batch']
    result = create_handover(ramp.book, previous_batch, incoming)
    ft99 = next(row for row in result['trucks'] if row['truck'] == 'FT-99')
    assert ft99['state'] == 'HANDOVER_HELD' and ft99['difference_g'] is None
    assert result['held_trucks'] == 1
    anchored = [row for row in result['trucks'] if row['state'] != 'HANDOVER_HELD']
    assert sum(row['difference_g'] for row in anchored) == result['total_difference_g']
    assert handover(ramp.book, result['digest'])['handover']['digest'] == result['digest']
