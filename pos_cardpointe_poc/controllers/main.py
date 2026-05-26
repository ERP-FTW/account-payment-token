import logging
import threading
import uuid

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient
from odoo.addons.payment_cardpointe_base.services.money import format_gateway_amount

from ..services.cardpointe_terminal import CardPointeTerminalClient
from ..services.signature_policy import (
    amount_meets_threshold,
    emv_indicates_signature_applicable,
    parse_emv_tag_data,
)

_logger = logging.getLogger(__name__)


class PosCardPointeController(http.Controller):
    _active_requests = {}
    _active_requests_lock = threading.Lock()

    @classmethod
    def _set_active_request(cls, request_id, values):
        with cls._active_requests_lock:
            cls._active_requests[request_id] = values

    @classmethod
    def _get_active_request(cls, request_id):
        with cls._active_requests_lock:
            return cls._active_requests.get(request_id)

    @classmethod
    def _pop_active_request(cls, request_id):
        with cls._active_requests_lock:
            return cls._active_requests.pop(request_id, None)

    def _validate_start_payload(self, pos_config_id, payment_method_id):
        pos_config = request.env['pos.config'].browse(int(pos_config_id)).exists()
        if not pos_config:
            return None, None, {'status': 'error', 'message': 'POS config not found.'}
        if pos_config.company_id not in request.env.user.company_ids:
            return None, None, {'status': 'error', 'message': 'Access denied for this POS config.'}

        payment_method = request.env['pos.payment.method'].browse(int(payment_method_id)).exists()
        if not payment_method or payment_method.use_payment_terminal != 'cardpointe_poc':
            return None, None, {'status': 'error', 'message': 'Invalid payment method.'}

        config = payment_method.cardpointe_config_id
        if not config:
            return None, None, {'status': 'error', 'message': 'CardPointe config missing on payment method.'}

        return payment_method, config, None

    def _signature_required_pre_auth(self, config, amount_dollars):
        mode = config.signature_mode or 'over_threshold'
        if mode == 'always':
            return True
        if mode == 'over_threshold':
            return amount_meets_threshold(amount_dollars, config.signature_threshold_amount)
        return False

    def _signature_required_on_policy(self, auth_result):
        emv_data = parse_emv_tag_data(auth_result.get('emvTagData'))
        return emv_indicates_signature_applicable(emv_data)

    def _resolve_merchant_config(self, terminal_config):
        merchant_config = terminal_config.merchant_config_id
        if merchant_config:
            return merchant_config

        merchant_config = request.env['cardpointe.merchant.config'].search([
            ('company_id', '=', terminal_config.company_id.id),
            ('mid', '=', terminal_config.merchant_id),
        ], limit=1)
        if merchant_config:
            terminal_config.merchant_config_id = merchant_config.id
        return merchant_config

    def _attach_signature_sigcap(self, terminal_config, retref, signature_blob):
        merchant_config = self._resolve_merchant_config(terminal_config)
        if not merchant_config or not merchant_config.gateway_username or not merchant_config.gateway_password:
            return {'ok': False, 'message': 'CardPointe gateway credentials are missing on merchant config for sigcap.'}
        gateway = CardPointeGatewayClient(merchant_config)
        return gateway.sigcap(merchid=merchant_config.mid, retref=retref, signature=signature_blob)

    @http.route('/pos_cardpointe_poc/manual_config', type='json', auth='user')
    def manual_config(self, pos_config_id, payment_method_id):
        payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error
        if not payment_method.cardpointe_manual_entry_enabled:
            return {'status': 'error', 'message': 'Manual Entry is disabled for this payment method.'}
        merchant_config = self._resolve_merchant_config(config)
        if not merchant_config:
            return {'status': 'error', 'message': 'CardPointe merchant config missing on terminal config.'}
        if not merchant_config.tokenizer_url:
            return {'status': 'error', 'message': 'CardPointe tokenizer URL is missing on merchant config.'}
        return {
            'status': 'ok',
            'tokenizer_url': merchant_config.tokenizer_url,
            'allowed_ecominds': [["E", "E - Ecommerce"], ["T", "T - Telephone/Mail"]],
            'default_ecomind': 'E',
            'manual_entry_enabled': True,
        }

    @http.route('/pos_cardpointe_poc/manual_auth', type='json', auth='user')
    def manual_auth(self, pos_config_id, payment_method_id, amount, currency, order_uid, payment_line_uuid, token,
                    ecomind='E', partner_id=None, fallback_reason=None, terminal_error_status=None, terminal_error_message=None,
                    cardholder_name=None, billing_address=None):
        payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error
        if not payment_method.cardpointe_manual_entry_enabled:
            return {'status': 'error', 'message': 'Manual Entry is disabled for this payment method.'}
        if not token:
            return {'status': 'error', 'message': 'Missing CardPointe token for manual entry.'}
        merchant_config = self._resolve_merchant_config(config)
        if not merchant_config:
            return {'status': 'error', 'message': 'CardPointe merchant config missing on terminal config.'}
        if not merchant_config.gateway_username or not merchant_config.gateway_password:
            return {'status': 'error', 'message': 'CardPointe gateway credentials are missing on merchant config.'}

        ecomind = (ecomind or 'E').strip().upper()
        if ecomind not in ('E', 'T'):
            ecomind = 'E'
        _logger.info('[CARDPOINTE POS MANUAL] user=%s pos_config_id=%s payment_method_id=%s amount=%s order_uid=%s line=%s token_present=%s ecomind=%s fallback_reason=%s',
            request.env.user.id, pos_config_id, payment_method_id, amount, order_uid, payment_line_uuid, bool(token), ecomind, fallback_reason or '')

        payload = {
            'merchid': merchant_config.mid,
            'account': token,
            'amount': format_gateway_amount(amount),
            'currency': currency or 'USD',
            'capture': 'Y',
            'orderid': order_uid,
            'ecomind': ecomind,
        }
        if cardholder_name:
            payload['name'] = cardholder_name
        partner = request.env['res.partner'].browse(int(partner_id)).exists() if partner_id else request.env['res.partner']
        if partner:
            payload.update({
                'name': payload.get('name') or partner.name,
                'address': partner.street,
                'city': partner.city,
                'region': partner.state_id.code if partner.state_id else '',
                'country': partner.country_id.code if partner.country_id else '',
                'postal': partner.zip,
            })
        if isinstance(billing_address, dict):
            payload.update({k: v for k, v in billing_address.items() if k in {'address', 'city', 'region', 'country', 'postal', 'name'} and v})

        result = CardPointeGatewayClient(merchant_config).auth(payload)
        approved = bool(result.get('ok'))
        return {
            'status': 'approved' if approved else ('declined' if result.get('respcode') else 'error'),
            'capture_method': 'iframe_manual',
            'retref': result.get('retref') or '',
            'authcode': result.get('authcode') or '',
            'respcode': result.get('respcode') or '',
            'resptext': result.get('resptext') or result.get('message') or '',
            'token': result.get('token') or '',
            'entrymode': 'iframe_manual',
            'ecomind': ecomind,
            'amount': payload['amount'],
            'ok': approved,
            'http_status': result.get('http_status'),
            'fallback_reason': fallback_reason or 'manual_selected',
            'terminal_error_status': terminal_error_status or '',
            'terminal_error_message': terminal_error_message or '',
        }

    # keep existing routes below

    @http.route('/pos_cardpointe_poc/start', type='json', auth='user')
    def start(self, pos_config_id, payment_method_id, amount, currency, order_uid, payment_line_uuid):
        _logger.info(
            "CardPointe start user=%s pos_config_id=%s payment_method_id=%s amount=%s currency=%s order_uid=%s line=%s",
            request.env.user.id,
            pos_config_id,
            payment_method_id,
            amount,
            currency,
            order_uid,
            payment_line_uuid,
        )

        _payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error

        connect_result = CardPointeTerminalClient(config).connect()
        if not connect_result.get('ok'):
            _logger.warning(
                "CardPointe start failed to connect user=%s payment_method_id=%s reason=%s",
                request.env.user.id,
                payment_method_id,
                connect_result.get('message'),
            )
            return {
                'status': connect_result.get('status', 'error'),
                'message': connect_result.get('message') or 'Unable to connect terminal session.',
            }

        request_id = str(uuid.uuid4())
        self._set_active_request(
            request_id,
            {
                'session_key': connect_result['session_key'],
                'config_id': config.id,
                'payment_method_id': int(payment_method_id),
                'uid': request.env.uid,
                'order_uid': order_uid,
                'payment_line_uuid': payment_line_uuid,
                'amount': amount,
            },
        )
        _logger.info(
            "CardPointe terminal session established request_id=%s payment_method_id=%s order_uid=%s",
            request_id,
            payment_method_id,
            order_uid,
        )
        return {'status': 'ready', 'request_id': request_id}

    @http.route('/pos_cardpointe_poc/auth', type='json', auth='user')
    def auth(self, request_id):
        active_request = self._get_active_request(request_id)
        if not active_request:
            _logger.warning("CardPointe auth with unknown request_id=%s user=%s", request_id, request.env.user.id)
            return {
                'status': 'error',
                'message': 'Card terminal session expired. Start payment again.',
            }

        if active_request['uid'] != request.env.uid:
            _logger.warning(
                "CardPointe auth access denied request_id=%s expected_uid=%s got_uid=%s",
                request_id,
                active_request['uid'],
                request.env.uid,
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = request.env['pos.payment.method'].browse(active_request['payment_method_id']).exists()
        if not payment_method:
            self._pop_active_request(request_id)
            return {'status': 'error', 'message': 'Payment method no longer available.'}
        config = payment_method.cardpointe_config_id
        if not config:
            self._pop_active_request(request_id)
            return {'status': 'error', 'message': 'CardPointe config missing on payment method.'}

        _logger.info("CardPointe auth started request_id=%s", request_id)
        terminal_client = CardPointeTerminalClient(config)

        signature_required_pre_auth = self._signature_required_pre_auth(config, active_request['amount'])

        try:
            result = terminal_client.auth_card_with_session(
                amount_dollars=active_request['amount'],
                order_id=active_request['order_uid'],
                session_key=active_request['session_key'],
                include_signature=signature_required_pre_auth,
            )

            signature_required = signature_required_pre_auth
            signature_captured = bool(result.get('signature_captured_inline')) if signature_required_pre_auth else False
            signature_method = 'inline_authcard' if signature_required_pre_auth else ''
            if result.get('status') == 'approved' and config.signature_mode == 'on_policy':
                signature_required = self._signature_required_on_policy(result)
                signature_captured = False
                signature_method = 'post_readSignature' if signature_required else ''

                if signature_required:
                    read_sig_result = terminal_client.read_signature(active_request['session_key'])
                    if not read_sig_result.get('ok'):
                        _logger.warning(
                            "CardPointe on_policy readSignature failed request_id=%s reason=%s",
                            request_id,
                            read_sig_result.get('message'),
                        )
                    else:
                        signature_captured = True
                        if result.get('retref'):
                            sigcap_result = self._attach_signature_sigcap(
                                config,
                                retref=result.get('retref'),
                                signature_blob=read_sig_result.get('signature'),
                            )
                            if not sigcap_result.get('ok'):
                                _logger.warning(
                                    "CardPointe on_policy sigcap failed request_id=%s reason=%s",
                                    request_id,
                                    sigcap_result.get('message'),
                                )

            if result.get('status') == 'approved':
                return {
                    'status': 'approved',
                    'retref': result.get('retref'),
                    'authcode': result.get('authcode'),
                    'respcode': result.get('respcode'),
                    'resptext': result.get('resptext'),
                    'amount': result.get('amount'),
                    'token': result.get('token'),
                    'entrymode': result.get('entrymode'),
                    'emvTagData': result.get('emvTagData'),
                    'signature_required': signature_required,
                    'signature_captured': signature_captured,
                    'signature_method': signature_method,
                }

            return {
                'status': result.get('status', 'error'),
                'message': result.get('message') or result.get('resptext') or 'Terminal payment failed.',
                'respcode': result.get('respcode'),
                'resptext': result.get('resptext'),
            }
        finally:
            self._pop_active_request(request_id)
            disconnect_result = terminal_client.disconnect(active_request['session_key'])
            if not disconnect_result.get('ok'):
                _logger.warning(
                    "CardPointe auth cleanup disconnect failed request_id=%s reason=%s",
                    request_id,
                    disconnect_result.get('message'),
                )

    @http.route('/pos_cardpointe_poc/cancel', type='json', auth='user')
    def cancel(self, request_id):
        active_request = self._pop_active_request(request_id)
        if not active_request:
            _logger.warning("CardPointe cancel with unknown request_id=%s user=%s", request_id, request.env.user.id)
            return {
                'status': 'error',
                'message': 'No active CardPointe terminal request found to cancel.',
            }
        if active_request['uid'] != request.env.uid:
            _logger.warning(
                "CardPointe cancel access denied request_id=%s expected_uid=%s got_uid=%s",
                request_id,
                active_request['uid'],
                request.env.uid,
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = request.env['pos.payment.method'].browse(active_request['payment_method_id']).exists()
        if not payment_method or not payment_method.cardpointe_config_id:
            return {'status': 'error', 'message': 'CardPointe config no longer available for cancellation.'}

        terminal_client = CardPointeTerminalClient(payment_method.cardpointe_config_id)
        result = terminal_client.cancel(active_request['session_key'])
        disconnect_result = terminal_client.disconnect(active_request['session_key'])
        if not disconnect_result.get('ok'):
            _logger.warning(
                "CardPointe cancel cleanup disconnect failed request_id=%s reason=%s",
                request_id,
                disconnect_result.get('message'),
            )

        if result.get('ok'):
            return {
                'status': 'cancelled',
                'message': result.get('message') or 'Cancel accepted by terminal.',
                'respcode': result.get('respcode'),
                'resptext': result.get('resptext'),
            }

        _logger.warning(
            "CardPointe cancel failed request_id=%s message=%s respcode=%s resptext=%s",
            request_id,
            result.get('message'),
            result.get('respcode'),
            result.get('resptext'),
        )
        return {
            'status': 'error',
            'message': result.get('message') or 'Cancel failed.',
            'respcode': result.get('respcode'),
            'resptext': result.get('resptext'),
        }

    @http.route('/pos_cardpointe_poc/refund', type='json', auth='user')
    def refund(self, payment_method_id, amount, refunded_orderline_ids):
        payment_method = request.env['pos.payment.method'].browse(int(payment_method_id)).exists()
        if not payment_method or payment_method.use_payment_terminal != 'cardpointe_poc':
            return {'status': 'error', 'message': 'Invalid payment method.'}

        try:
            result = request.env['pos.payment'].cardpointe_process_refund(
                payment_method_id=payment_method.id,
                amount=amount,
                refunded_orderline_ids=refunded_orderline_ids or [],
            )
        except UserError as exc:
            message = exc.args[0] if getattr(exc, 'args', None) else str(exc)
            _logger.warning("CardPointe refund failed payment_method_id=%s reason=%s", payment_method.id, message)
            return {'status': 'error', 'message': message}

        return {
            'status': result.get('status', 'error'),
            'retref': result.get('retref'),
            'respcode': result.get('respcode'),
            'resptext': result.get('resptext'),
            'operation': result.get('operation'),
            'original_retref': result.get('original_retref'),
            'ok': result.get('ok', result.get('status') == 'approved'),
        }

    @http.route('/pos_cardpointe_poc/poll', type='json', auth='user')
    def poll(self, payment_method_id, request_id):
        return {
            'status': 'error',
            'message': 'Not implemented for authCard flow.',
        }
