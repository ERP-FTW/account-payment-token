import json
import logging

from odoo.addons.payment_cardpointe_base.services.http import (
    CardPointeRequestError,
    redact_payload,
    request_json,
    safe_log_headers,
    safe_truncate,
)
from odoo.addons.payment_cardpointe_base.services.money import dollars_to_implied_cents
from odoo.addons.payment_cardpointe_base.services.normalize import normalize_terminal_authcard_response

_logger = logging.getLogger(__name__)


class CardPointeTerminalClient:
    def __init__(self, config):
        self.config = config
        self.base_url = (config.base_url or '').rstrip('/')

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _headers(self, session_key=None):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Odoo/18 pos_cardpointe_poc',
            'Authorization': self.config.auth_key,
        }
        if session_key:
            headers['X-CardConnect-SessionKey'] = session_key
        return headers

    def _sanitize_for_log(self, data):
        if isinstance(data, dict):
            clean = {}
            for key, value in data.items():
                key_lower = str(key).lower()
                if any(s in key_lower for s in ('signature', 'receipt')):
                    continue
                if key_lower == 'emvtagdata':
                    clean[key] = safe_truncate(str(value), limit=200)
                    continue
                clean[key] = self._sanitize_for_log(value)
            return clean
        if isinstance(data, list):
            return [self._sanitize_for_log(item) for item in data]
        return data

    def _safe_body_for_log(self, text):
        body = text or ''
        stripped = body.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return safe_truncate(body)
            return self._sanitize_for_log(parsed)
        return safe_truncate(body)

    def _request(self, method, path, payload=None, session_key=None, timeout=30):
        url = self._url(path)
        headers = self._headers(session_key=session_key)
        _logger.info(
            "CardPointe request method=%s path=%s headers=%s payload=%s timeout=%s",
            method,
            path,
            safe_log_headers(headers),
            redact_payload(payload or {}),
            timeout,
        )
        try:
            status_code, response_headers, text, response_json = request_json(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except CardPointeRequestError as exc:
            message = str(exc)
            status = 'timeout' if 'timeout' in message.lower() else 'error'
            _logger.warning("CardPointe request failed method=%s path=%s reason=%s", method, path, message)
            return {
                'ok': False,
                'status': status,
                'message': 'Terminal request timed out.' if status == 'timeout' else message,
            }

        _logger.info(
            "CardPointe response method=%s path=%s status=%s body=%s",
            method,
            path,
            status_code,
            self._sanitize_for_log(response_json) if response_json else self._safe_body_for_log(text),
        )

        return {
            'ok': True,
            'http_status': status_code,
            'data': response_json or {},
            'headers': response_headers,
            'text': text,
        }

    def _map_connect_error(self, result):
        data = dict(result.get('data') or {})
        error_code = str(data.get('errorCode') or '')
        error_message = data.get('errorMessage') or f"Connect failed (HTTP {result.get('http_status')})."

        if error_code == '7' or 'already in use' in error_message.lower():
            _logger.warning(
                "CardPointe connect returned errorCode=7 (already in use). Treating as recoverable connect error, not active POS in_use state. raw_message=%s",
                error_message,
            )
            return {
                'ok': False,
                'status': 'in_use',
                'message': 'Terminal is in use, retry in a few seconds.',
                'raw': result,
            }

        if error_code == '9' or 'merchant mode' in error_message.lower():
            return {
                'ok': False,
                'status': 'merchant_mode',
                'message': 'Terminal is in Merchant Mode. Switch to CardPointe Integrated/Bolt app and retry.',
                'raw': result,
            }

        return {
            'ok': False,
            'status': 'error',
            'message': error_message,
            'raw': result,
        }

    def connect(self):
        payload = {
            'merchantId': self.config.merchant_id,
            'hsn': self.config.device_serial,
        }
        result = self._request('POST', '/v2/connect', payload=payload, timeout=15)
        if not result.get('ok'):
            return result

        header_value = result['headers'].get('X-CardConnect-SessionKey', '') or ''
        session_key = header_value.split(';', 1)[0].strip()
        if result['http_status'] != 200 or not session_key:
            return self._map_connect_error(result)

        return {'ok': True, 'session_key': session_key}

    def disconnect(self, session_key):
        payload = {
            'merchantId': self.config.merchant_id,
            'hsn': self.config.device_serial,
        }
        result = self._request(
            'POST',
            '/v2/disconnect',
            payload=payload,
            session_key=session_key,
            timeout=15,
        )
        if not result.get('ok'):
            return result

        is_ok = result.get('http_status') == 200
        _logger.info("CardPointe disconnect mapped ok=%s http_status=%s", is_ok, result.get('http_status'))
        return {
            'ok': is_ok,
            'status': 'ok' if is_ok else 'error',
            'message': 'Disconnect accepted by terminal.' if is_ok else 'Disconnect failed.',
            'raw': dict(result.get('data') or {}),
        }

    def cancel(self, session_key):
        payload = {
            'merchantId': self.config.merchant_id,
            'hsn': self.config.device_serial,
        }
        result = self._request(
            'POST',
            '/v2/cancel',
            payload=payload,
            session_key=session_key,
            timeout=15,
        )
        if not result.get('ok'):
            return result

        data = dict(result.get('data') or {})
        status = (data.get('status') or '').lower()
        resptext = data.get('resptext') or data.get('message') or ''
        ok_statuses = {'ok', 'success', 'canceled', 'cancelled'}
        is_ok = result.get('http_status') == 200 and (not status or status in ok_statuses)

        _logger.info(
            "CardPointe cancel mapped ok=%s http_status=%s status=%s resptext=%s",
            is_ok,
            result.get('http_status'),
            status,
            resptext,
        )

        return {
            'ok': is_ok,
            'status': 'cancelled' if is_ok else 'error',
            'message': resptext or ('Cancel accepted by terminal.' if is_ok else 'Cancel failed.'),
            'respcode': data.get('respcode'),
            'resptext': resptext,
            'raw': data,
        }

    def auth_card_with_session(self, amount_dollars, order_id, session_key, include_signature=False):
        payload = {
            'merchantId': self.config.merchant_id,
            'hsn': self.config.device_serial,
            'amount': dollars_to_implied_cents(amount_dollars),
            'capture': True,
            'orderId': order_id,
            'includeSignature': bool(include_signature),
        }
        receipt_requested = bool(self.config.print_receipt_on_terminal)
        if self.config.device_type == 'clover_flex':
            payload['printReceipt'] = receipt_requested
        _logger.info(
            "CardPointe authCard receipt printing requested=%s device_type=%s",
            receipt_requested if self.config.device_type == 'clover_flex' else False,
            self.config.device_type,
        )
        result = self._request(
            'POST',
            '/v4/authCard',
            payload=payload,
            session_key=session_key,
            timeout=max(30, self.config.request_timeout_seconds or 120),
        )
        if not result.get('ok'):
            return result

        http_status = result.get('http_status')
        data = dict(result.get('data') or {})
        signature_value = data.pop('signature', None)

        normalized = normalize_terminal_authcard_response(http_status, data)
        response_message = normalized.get('resptext') or 'Terminal payment failed.'

        _logger.info(
            "CardPointe authCard mapped status=%s ok=%s respcode=%s retref=%s entrymode=%s",
            normalized.get('status'),
            normalized.get('ok'),
            normalized.get('respcode'),
            normalized.get('retref'),
            normalized.get('entrymode'),
        )

        return {
            'ok': normalized.get('ok'),
            'status': normalized.get('status'),
            'message': response_message,
            'respcode': normalized.get('respcode'),
            'resptext': normalized.get('resptext'),
            'retref': normalized.get('retref'),
            'authcode': normalized.get('authcode'),
            'amount': data.get('amount'),
            'token': normalized.get('token'),
            'entrymode': normalized.get('entrymode'),
            'emvTagData': normalized.get('emvTagData'),
            'signature_captured_inline': bool(signature_value),
            'raw': data,
        }

    def read_signature(self, session_key):
        payload = {
            'merchantId': self.config.merchant_id,
            'hsn': self.config.device_serial,
        }
        result = self._request(
            'POST',
            '/v2/readSignature',
            payload=payload,
            session_key=session_key,
            timeout=max(20, self.config.request_timeout_seconds or 120),
        )
        if not result.get('ok'):
            return result

        data = dict(result.get('data') or {})
        signature = data.get('signature')
        if result.get('http_status') != 200 or not signature:
            return {
                'ok': False,
                'status': 'error',
                'message': data.get('resptext') or data.get('errorMessage') or 'Signature capture failed.',
                'raw': self._sanitize_for_log(data),
            }
        return {
            'ok': True,
            'signature': signature,
            'raw': self._sanitize_for_log(data),
        }

    def auth_card(self, amount_dollars, order_id, include_signature=False):
        connect_result = self.connect()
        if not connect_result.get('ok'):
            return {
                'ok': False,
                'status': connect_result.get('status', 'error'),
                'message': connect_result.get('message', 'Unable to connect terminal session.'),
            }

        return self.auth_card_with_session(
            amount_dollars=amount_dollars,
            order_id=order_id,
            session_key=connect_result['session_key'],
            include_signature=include_signature,
        )
