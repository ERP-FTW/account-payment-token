from .gateway import sanitize_for_log


def _extract_data(response):
    if isinstance(response, dict) and isinstance(response.get('data'), dict):
        return response.get('data') or {}
    return response if isinstance(response, dict) else {}


def _is_settled_for_refund(resp):
    data = _extract_data(resp)
    text = (data.get('resptext') or '').lower()
    code = str(data.get('respcode') or '')
    return code in {'12', '400'} or 'settled' in text or 'batched' in text


def _is_approved(resp):
    data = _extract_data(resp)
    respstat = (data.get('respstat') or '').upper()
    respcode = str(data.get('respcode') or '')
    resptext = (data.get('resptext') or '').strip().lower()
    return respstat == 'A' or respcode in {'000', '00'} or resptext.startswith('approv')


def _is_already_voided(resp):
    data = _extract_data(resp)
    text = (data.get('resptext') or '').strip().lower()
    code = str(data.get('respcode') or '')
    return code in {'24'} and ('reversal not supported' in text or 'already' in text and 'void' in text)

def is_txn_not_settled(resp):
    data = _extract_data(resp)
    return data.get('respcode') == '28' or 'not settled' in (data.get('resptext') or '').lower()


def choose_operation_from_inquire(inquire):
    data = _extract_data(inquire)
    settle_status = str(data.get('setlstat') or '').strip().lower()
    if not settle_status:
        return 'void'

    settled_values = {
        '1', 'y', 'yes', 'settled', 'settle', 'complete', 'completed', 'captured', 'batched', 'batch',
    }
    return 'refund' if settle_status in settled_values else 'void'


def _normalize_result(operation, retref, response, raw=None):
    data = _extract_data(response)
    return {
        'ok': _is_approved(response) or (operation == 'void' and _is_already_voided(response)),
        'operation': operation,
        'respstat': data.get('respstat'),
        'respcode': data.get('respcode'),
        'resptext': data.get('resptext'),
        'retref': data.get('retref') or retref,
        'authcode': data.get('authcode'),
        'raw': sanitize_for_log(raw or data),
    }


def execute_void_or_refund(gw_client, merchid, retref, amount, orderid=None):
    inquire = gw_client.inquire(retref, merchid)
    operation = choose_operation_from_inquire(inquire)
    inquire_data = _extract_data(inquire)

    if str(inquire_data.get('setlstat') or '').strip().lower() == 'voided':
        synthetic = {
            'respstat': 'A',
            'respcode': '000',
            'resptext': 'Approval',
            'retref': inquire_data.get('retref') or retref,
            'authcode': inquire_data.get('authcode'),
        }
        return _normalize_result('void', retref, synthetic, {
            'inquire': inquire_data,
            'orderid': orderid,
            'note': 'already_voided',
        })

    if operation == 'void':
        void_result = gw_client.void(merchid, retref)
        if not _is_approved(void_result) and amount and _is_settled_for_refund(void_result):
            refund_result = gw_client.refund(merchid, retref, amount)
            return _normalize_result('refund', retref, refund_result, {
                'inquire': inquire_data,
                'void': _extract_data(void_result),
                'refund': _extract_data(refund_result),
                'orderid': orderid,
            })
        return _normalize_result('void', retref, void_result, {
            'inquire': inquire_data,
            'void': _extract_data(void_result),
            'orderid': orderid,
        })

    refund_result = gw_client.refund(merchid, retref, amount)
    if is_txn_not_settled(refund_result):
        void_result = gw_client.void(merchid, retref)
        return _normalize_result('void', retref, void_result, {
            'inquire': inquire_data,
            'refund': _extract_data(refund_result),
            'void': _extract_data(void_result),
            'orderid': orderid,
        })

    return _normalize_result('refund', retref, refund_result, {
        'inquire': inquire_data,
        'refund': _extract_data(refund_result),
        'orderid': orderid,
    })
