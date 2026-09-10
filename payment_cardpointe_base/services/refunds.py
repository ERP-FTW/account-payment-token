from decimal import Decimal, InvalidOperation

from .gateway import sanitize_for_log


_SUCCESS_CODES = {'000', '00'}
_SETTLED_VALUES = {
    '1', 'y', 'yes', 'accepted', 'settled', 'settle', 'complete', 'completed',
    'captured', 'batched', 'batch',
}


def _extract_data(response):
    if isinstance(response, dict) and isinstance(response.get('data'), dict):
        return response.get('data') or {}
    return response if isinstance(response, dict) else {}


def _response_message(response):
    data = _extract_data(response)
    if isinstance(response, dict):
        return response.get('message') or response.get('error') or data.get('resptext')
    return data.get('resptext')


def _has_explicit_failure(response):
    data = _extract_data(response)
    status = str(data.get('respstat') or '').strip().upper()
    code = str(data.get('respcode') or '').strip()
    return status in {'D', 'E'} or (code and code not in _SUCCESS_CODES)


def _is_approved(resp):
    data = _extract_data(resp)
    if _has_explicit_failure(resp):
        return False
    respstat = (data.get('respstat') or '').upper()
    respcode = str(data.get('respcode') or '')
    resptext = (data.get('resptext') or '').strip().lower()
    return respstat == 'A' or respcode in _SUCCESS_CODES or resptext.startswith('approv')


def _is_settled_for_refund(resp):
    data = _extract_data(resp)
    text = (data.get('resptext') or '').lower()
    code = str(data.get('respcode') or '')
    return code in {'12', '400'} or 'settled' in text or 'batched' in text


def is_txn_not_settled(resp):
    data = _extract_data(resp)
    return str(data.get('respcode') or '') == '28' or 'not settled' in (data.get('resptext') or '').lower()


def _flag(data, name):
    value = data.get(name)
    return None if value is None else str(value).strip().lower() in {'y', 'yes', 'true', '1'}


def _money(value):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _is_full_amount(original_amount, requested_amount):
    original = _money(original_amount)
    requested = _money(requested_amount)
    return original is not None and requested is not None and original == requested


def _void_allowed(data, requested_amount):
    return _is_full_amount(data.get('amount'), requested_amount) and _flag(data, 'voidable') is not False


def _refund_allowed(data):
    return _flag(data, 'refundable') is not False


def choose_operation_from_inquire(inquire, amount=None):
    data = _extract_data(inquire)
    if not _is_full_amount(data.get('amount'), amount):
        return 'refund'
    settle_status = str(data.get('setlstat') or '').strip().lower()
    return 'refund' if settle_status in _SETTLED_VALUES else 'void'


def _normalize_result(operation, retref, response, raw=None):
    data = _extract_data(response)
    return {
        'ok': _is_approved(response),
        'operation': operation,
        'respstat': data.get('respstat'),
        'respcode': data.get('respcode'),
        'resptext': data.get('resptext'),
        'message': _response_message(response),
        'retref': data.get('retref') or retref,
        'authcode': data.get('authcode'),
        'raw': sanitize_for_log(raw or data),
    }


def execute_void_or_refund(gw_client, merchid, retref, amount, orderid=None):
    requested_amount = _money(amount)
    inquire = gw_client.inquire(retref, merchid)
    inquire_data = _extract_data(inquire)

    if not isinstance(inquire, dict) or inquire.get('ok') is False or _has_explicit_failure(inquire):
        return _normalize_result('inquire', retref, inquire, {
            'inquire': inquire_data,
            'orderid': orderid,
        })
    if str(inquire_data.get('setlstat') or '').strip().lower() == 'voided':
        return _normalize_result('inquire', retref, {
            'respstat': 'D',
            'respcode': '24',
            'resptext': 'Transaction is already voided',
            'retref': inquire_data.get('retref') or retref,
        }, {'inquire': inquire_data, 'orderid': orderid})
    if requested_amount is None:
        return _normalize_result('inquire', retref, {
            'respstat': 'D', 'respcode': '13',
            'resptext': 'Refund amount must be a positive decimal amount',
        }, {'inquire': inquire_data, 'orderid': orderid})

    full_amount = _is_full_amount(inquire_data.get('amount'), requested_amount)
    operation = choose_operation_from_inquire(inquire, requested_amount)
    if operation == 'void' and not _void_allowed(inquire_data, requested_amount):
        operation = 'refund'
    if operation == 'refund' and not _refund_allowed(inquire_data):
        return _normalize_result('refund', retref, {
            'respstat': 'D', 'respcode': '26', 'resptext': 'Transaction is not refundable',
        }, {'inquire': inquire_data, 'orderid': orderid})

    if operation == 'void':
        void_result = gw_client.void(merchid, retref)
        if (not _is_approved(void_result) and full_amount and _void_allowed(inquire_data, requested_amount)
                and _is_settled_for_refund(void_result) and _refund_allowed(inquire_data)):
            refund_result = gw_client.refund(merchid, retref, amount)
            return _normalize_result('refund', retref, refund_result, {
                'inquire': inquire_data, 'void': _extract_data(void_result),
                'refund': _extract_data(refund_result), 'orderid': orderid,
            })
        return _normalize_result('void', retref, void_result, {
            'inquire': inquire_data, 'void': _extract_data(void_result), 'orderid': orderid,
        })

    refund_result = gw_client.refund(merchid, retref, amount)
    return _normalize_result('refund', retref, refund_result, {
        'inquire': inquire_data, 'refund': _extract_data(refund_result), 'orderid': orderid,
    })
