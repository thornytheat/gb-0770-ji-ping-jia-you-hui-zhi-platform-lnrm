"""罐车两班交接对账。

接班按本班复秤实数开账，本班余额表不因交接改写；交接差（接班期初复秤减上班
签单期末）只在交接账里记一次，短少挂在上班签单名下留作追责。上班原件只读取
已归档快照，绝不回改。
"""
from app.diagnostics import detail


def _anchor(snapshot, truck, kind):
    """从已归档批次的余额表取某车某库存类型的唯一锚点。"""
    for stock in snapshot['stages']['balance']['stocks']:
        if stock['truck'] != truck:
            continue
        if not stock['complete']:
            return None
        return stock['closing_g'] if kind == 'closing' else stock['opening_g']
    return None


def _lines(snapshot, truck, kind):
    for stock in snapshot['stages']['balance']['stocks']:
        if stock['truck'] == truck:
            return stock['source_lines'] if stock['complete'] else stock['isolated_lines']
    return []


def reconcile_handover(previous, incoming):
    prev_balance = previous['stages']['balance']
    incoming_balance = incoming['stages']['balance']
    trucks = sorted({row['truck'] for row in prev_balance['stocks']} |
                    {row['truck'] for row in incoming_balance['stocks']})
    entries = []
    for truck in trucks:
        signed_end = _anchor(previous, truck, 'closing')
        reweighed_start = _anchor(incoming, truck, 'opening')
        held = signed_end is None or reweighed_start is None
        if held:
            state = 'HANDOVER_HELD'
            delta = None
        else:
            delta = reweighed_start - signed_end
            state = 'HANDOVER_SHORT' if delta < 0 else 'HANDOVER_OVER' if delta > 0 else 'HANDOVER_MATCH'
        entries.append({'truck': truck, 'state': state, 'reason': detail(state)['note'],
                        'signed_closing_g': signed_end, 'reweighed_opening_g': reweighed_start,
                        'difference_g': delta,
                        'previous_lines': _lines(previous, truck, 'closing'),
                        'incoming_lines': _lines(incoming, truck, 'opening')})
    complete = [row for row in entries if row['state'] != 'HANDOVER_HELD']
    return {'previous_batch': previous['manifest']['batch'], 'incoming_batch': incoming['manifest']['batch'],
            'trucks': entries,
            'truck_count': len(entries), 'complete_trucks': len(complete),
            'held_trucks': len(entries) - len(complete),
            'short_count': sum(row['state'] == 'HANDOVER_SHORT' for row in entries),
            'over_count': sum(row['state'] == 'HANDOVER_OVER' for row in entries),
            'total_difference_g': sum(row['difference_g'] for row in complete),
            'short_g': sum(row['difference_g'] for row in complete if row['difference_g'] < 0),
            'over_g': sum(row['difference_g'] for row in complete if row['difference_g'] > 0)}
