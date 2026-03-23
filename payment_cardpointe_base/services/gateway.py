import json
import logging

from .http import CardPointeRequestError, redact_payload, request_json, safe_log_headers, safe_truncate
from .money import format_gateway_amount

_logger = logging.getLogger(__name__)


def sanitize_for_log(data):
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if any(s in key_lower for s in ('signature', 'receipt', 'emvtagdata')):
                continue
            cleaned_value = sanitize_for_log(value)
            if cleaned_value in ({}, [], None, ''):
                continue
            sanitized[key] = cleaned_value
        return sanitized
    if isinstance(data, list):
        return [sanitize_for_log(item) for item in data]
    if isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return safe_truncate(data, limit=300)
            return sanitize_for_log(parsed)
        return safe_truncate(data, limit=300)
    return data


class CardPointeGatewayClient:
    def __init__(self, config):
        self.config = config
        self.base_url = (config.gateway_base_url or '').rstrip('/')

    def _url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(self, method, path, payload=None, timeout=30):
        url = self._url(path)
        headers = {'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'

        log_headers = dict(headers)
        log_headers['Authorization'] = f"Basic {self.config.gateway_username or ''}:***"

        _logger.info(
            "CardPointe gateway request method=%s path=%s headers=%s payload=%s",
            method,
            path,
            safe_log_headers(log_headers),
            redact_payload(payload or {}),
        )

        try:
            status_code, response_headers, text, response_json = request_json(
                method=method,
                url=url,
                headers=headers,
                json=payload,
                timeout=timeout,
                auth=(self.config.gateway_username or '', self.config.gateway_password or ''),
            )
        except CardPointeRequestError as exc:
            message = str(exc)
            _logger.warning("CardPointe gateway request failed method=%s path=%s reason=%s", method, path, message)
            return {
                'ok': False,
                'http_status': None,
                'data': {},
                'message': 'Gateway request timed out.' if 'timeout' in message.lower() else message,
                'error_code': 'timeout' if 'timeout' in message.lower() else 'request_error',
            }

        _logger.info(
            "CardPointe gateway response method=%s path=%s status=%s body=%s",
            method,
            path,
            status_code,
            sanitize_for_log(response_json or {'body': safe_truncate(text)}),
        )

        data = response_json or {}
        if status_code >= 400:
            return {
                'ok': False,
                'http_status': status_code,
                'data': data,
                'message': data.get('resptext') or data.get('error') or safe_truncate(text),
                'error_code': data.get('respcode') or 'http_error',
            }

        return {
            'ok': True,
            'http_status': status_code,
            'data': data,
            'headers': response_headers,
        }

    def inquire(self, retref, merchid):
        return self._request('GET', f"inquire/{retref}/{merchid}", timeout=20)

    def void(self, merchid, retref):
        return self._request('POST', 'void', payload={'merchid': merchid, 'retref': retref}, timeout=30)

    def refund(self, merchid, retref, amount):
        return self._request(
            'POST',
            'refund',
            payload={'merchid': merchid, 'retref': retref, 'amount': format_gateway_amount(amount)},
            timeout=30,
        )

    def sigcap(self, merchid, retref, signature):
        return self._request(
            'POST',
            'sigcap',
            payload={'merchid': merchid, 'retref': retref, 'signature': signature},
            timeout=30,
        )
