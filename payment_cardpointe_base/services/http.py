import logging
from json import JSONDecodeError


_logger = logging.getLogger(__name__)

REDACTED_HEADERS = {'authorization', 'x-cardconnect-sessionkey'}
SENSITIVE_KEYS = {
    'authorization',
    'password',
    'token',
    'card',
    'pan',
    'cvv',
    'cvc',
    'number',
    'account',
    'signature',
    'key',
    'session',
}


class CardPointeRequestError(Exception):
    """Raised when an HTTP request fails before receiving an HTTP response."""


def _mask_secret(value):
    if not value:
        return '***'
    text = str(value)
    if len(text) <= 8:
        return '***'
    return f"{text[:4]}...{text[-4:]}"


def safe_log_headers(headers):
    safe = {}
    for key, value in (headers or {}).items():
        if str(key).lower() in REDACTED_HEADERS:
            safe[key] = f"masked:{_mask_secret(value)}"
        else:
            safe[key] = value
    return safe


def safe_truncate(text, limit=500):
    value = text or ''
    return value if len(value) <= limit else f"{value[:limit]}..."


def _mask_token(value):
    if not value or not isinstance(value, str):
        return '****'
    return f"****{value[-4:]}" if len(value) >= 4 else '****'


def redact_payload(obj):
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            key_lower = str(key).lower()
            if any(sensitive in key_lower for sensitive in SENSITIVE_KEYS):
                redacted[key] = _mask_token(value) if 'token' in key_lower else '***'
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    return obj


def request_json(method, url, headers=None, json=None, timeout=30, verify=True, auth=None):
    """Perform an HTTP request and return (status_code, headers_dict, text, json_or_none)."""
    try:
        import requests

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
            verify=verify,
            auth=auth,
        )
    except ModuleNotFoundError as exc:
        raise CardPointeRequestError('requests library is not installed') from exc
    except requests.Timeout as exc:
        raise CardPointeRequestError("timeout") from exc
    except requests.RequestException as exc:
        raise CardPointeRequestError(str(exc)) from exc

    response_json = None
    if response.text:
        try:
            response_json = response.json()
        except JSONDecodeError:
            response_json = None

    return response.status_code, dict(response.headers), response.text or '', response_json
