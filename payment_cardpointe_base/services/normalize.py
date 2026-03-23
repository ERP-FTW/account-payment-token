def normalize_terminal_authcard_response(http_status, data_or_text):
    data = data_or_text if isinstance(data_or_text, dict) else {}
    normalized = {
        'status': 'error',
        'ok': False,
        'respstat': (data.get('respstat') or '').strip(),
        'respcode': str(data.get('respcode') or '').strip(),
        'resptext': data.get('resptext') or data.get('errorMessage') or data.get('message') or '',
        'retref': data.get('retref'),
        'authcode': data.get('authcode'),
        'token': data.get('token'),
        'entrymode': data.get('entrymode'),
        'emvTagData': data.get('emvTagData'),
    }

    error_code = data.get('errorCode')
    if error_code == 7:
        normalized['status'] = 'in_use'
        return normalized
    if error_code == 8:
        normalized['status'] = 'cancelled'
        return normalized
    if error_code == 9:
        normalized['status'] = 'merchant_mode'
        return normalized
    if error_code:
        normalized['status'] = 'error'
        return normalized

    respstat = normalized['respstat'].upper()
    respcode = normalized['respcode']
    resptext = (normalized['resptext'] or '').lower()

    if respstat == 'A' or respcode in ('000', '00') or resptext.startswith('approv'):
        normalized['status'] = 'approved'
        normalized['ok'] = True
    elif respstat == 'C':
        normalized['status'] = 'declined'
    elif respstat == 'B':
        normalized['status'] = 'retry'
    elif http_status and http_status >= 500:
        normalized['status'] = 'timeout'
    elif http_status and http_status >= 400:
        normalized['status'] = 'error'
    return normalized


def normalize_gateway_inquire_response(http_status, data_or_text):
    data = data_or_text if isinstance(data_or_text, dict) else {}
    return {
        'ok': http_status and 200 <= http_status < 300,
        'http_status': http_status,
        'data': data,
    }
