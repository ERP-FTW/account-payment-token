# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import uuid
from urllib.parse import urlsplit

from odoo import _
from odoo.addons.payment_cardpointe_base.services.http import (
    CardPointeRequestError,
    redact_payload,
    request_json,
    safe_log_headers,
    safe_truncate,
)
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _cardpointe_build_url(api_base, endpoint):
    """Join base + endpoint safely without dropping base path."""
    api_base = (api_base or "").rstrip("/")
    endpoint = (endpoint or "").lstrip("/")
    return f"{api_base}/{endpoint}"


def _cardpointe_request(provider, method, endpoint, payload=None, headers=None, timeout=None):
    """Perform a CardPointe API request and return a normalized response dict."""
    if not provider.cardpointe_api_base:
        raise UserError(_("CardPointe: missing API base URL."))

    correlation_id = uuid.uuid4().hex
    tx_reference = provider.env.context.get('cardpointe_tx_reference')
    operation = f"{method} {endpoint}"

    api_base = provider.cardpointe_api_base
    url = _cardpointe_build_url(api_base, endpoint)

    try:
        base_path = urlsplit(api_base).path or ""
        if "/cardconnect/rest" not in base_path:
            _logger.warning(
                "[CARDPOINTE] api_base may be misconfigured (missing /cardconnect/rest): api_base=%s",
                api_base,
            )
    except Exception:
        pass

    request_headers = headers.copy() if headers else {}
    if 'Accept' not in request_headers:
        request_headers['Accept'] = 'application/json'
    if payload is not None and 'Content-Type' not in request_headers:
        request_headers['Content-Type'] = 'application/json'

    if provider.cardpointe_debug_logging:
        _logger.info(
            "[CARDPOINTE] request method=%s endpoint=%s url=%s tx_ref=%s correlation_id=%s headers=%s payload=%s",
            method,
            endpoint,
            url,
            tx_reference or '-',
            correlation_id,
            safe_log_headers(request_headers),
            redact_payload(payload or {}),
        )

    _logger.info(
        "[CARDPOINTE] operation=%s url=%s tx_ref=%s correlation_id=%s",
        operation, url, tx_reference or '-', correlation_id,
    )

    connect_timeout = provider.cardpointe_timeout_connect
    read_timeout = provider.cardpointe_timeout_read
    request_timeout = timeout or (connect_timeout, read_timeout)

    try:
        http_status, _response_headers, response_text, response_data = request_json(
            method=method,
            url=url,
            headers=request_headers,
            json=payload,
            timeout=request_timeout,
            auth=(provider.cardpointe_username or '', provider.cardpointe_password or ''),
        )
    except CardPointeRequestError as exc:
        lower = str(exc).lower()
        error_code = 'timeout' if 'timeout' in lower else 'request_error'
        error_message = _("CardPointe request timed out.") if error_code == 'timeout' else str(exc)
        if 'name or service not known' in lower or 'failed to establish a new connection' in lower:
            error_code = 'connection_error'
            error_message = _("CardPointe connection error.")
        return {
            'ok': False,
            'data': {},
            'error_message': error_message,
            'error_code': error_code,
            'http_status': None,
            'correlation_id': correlation_id,
        }

    response_data = response_data or {}
    response_text_snippet = safe_truncate(response_text, limit=200)

    if provider.cardpointe_debug_logging:
        body = redact_payload(response_data) if response_data else response_text_snippet
        _logger.info(
            "[CARDPOINTE] response correlation_id=%s http_status=%s body=%s",
            correlation_id,
            http_status,
            body,
        )

    if http_status >= 400:
        _logger.warning(
            "[CARDPOINTE] http error method=%s url=%s correlation_id=%s http_status=%s body=%s",
            method,
            url,
            correlation_id,
            http_status,
            redact_payload(response_data) if response_data else response_text_snippet,
        )
        error_message = response_data.get('message') or response_data.get('error')
        if not error_message and response_text_snippet:
            error_message = response_text_snippet

        return {
            'ok': False,
            'data': response_data or {'raw': response_text_snippet},
            'error_message': error_message or _("CardPointe request failed."),
            'error_code': response_data.get('code') if response_data else None,
            'http_status': http_status,
            'correlation_id': correlation_id,
        }

    return {
        'ok': True,
        'data': response_data,
        'error_message': '',
        'error_code': None,
        'http_status': http_status,
        'correlation_id': correlation_id,
    }
