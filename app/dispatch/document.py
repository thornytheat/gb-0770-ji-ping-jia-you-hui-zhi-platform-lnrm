def compile_slip(archive, handover=None):
    stages = archive['stages']
    slip = {'document':'机坪加注证据单','manifest':archive['manifest'],
            'conservation':stages['balance'],'counter_checks':stages['mass']['rows'],
            'sources':stages['intake']['sheets']}
    if handover is not None:
        slip['handover'] = handover['handover']
        slip['previous_sources'] = handover['previous_snapshot']['sources']
    return slip
