import logging
import os
import threading
import uuid

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient

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
    def _diag_context(cls):
        return {
            'pid': os.getpid(),
            'thread_id': threading.get_ident(),
        }

    @classmethod
    def _active_request_count(cls):
        with cls._active_requests_lock:
            return len(cls._active_requests)

    @classmethod
    def _set_active_request(cls, request_id, values):
        with cls._active_requests_lock:
            cls._active_requests[request_id] = values
            size = len(cls._active_requests)
        diag = cls._diag_context()
        _logger.info(
            "CardPointe active_request set request_id=%s pid=%s thread_id=%s active_count=%s "
            "config_id=%s payment_method_id=%s uid=%s order_uid=%s payment_line_uuid=%s",
            request_id,
            diag['pid'],
            diag['thread_id'],
            size,
            values.get('config_id'),
            values.get('payment_method_id'),
            values.get('uid'),
            values.get('order_uid'),
            values.get('payment_line_uuid'),
        )

    @classmethod
    def _get_active_request(cls, request_id):
        with cls._active_requests_lock:
            active_request = cls._active_requests.get(request_id)
            size = len(cls._active_requests)
        diag = cls._diag_context()
        _logger.info(
            "CardPointe active_request get request_id=%s found=%s pid=%s thread_id=%s active_count=%s",
            request_id,
            bool(active_request),
            diag['pid'],
            diag['thread_id'],
            size,
        )
        return active_request

    @classmethod
    def _pop_active_request(cls, request_id):
        with cls._active_requests_lock:
            active_request = cls._active_requests.pop(request_id, None)
            size = len(cls._active_requests)
        diag = cls._diag_context()
        _logger.info(
            "CardPointe active_request pop request_id=%s found=%s pid=%s thread_id=%s active_count=%s",
            request_id,
            bool(active_request),
            diag['pid'],
            diag['thread_id'],
            size,
        )
        return active_request

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
            return {
                'ok': False,
                'message': 'CardPointe gateway credentials are missing on merchant config for sigcap.',
            }

        gateway = CardPointeGatewayClient(merchant_config)
        return gateway.sigcap(
            merchid=merchant_config.mid,
            retref=retref,
            signature=signature_blob,
        )

    @http.route('/pos_cardpointe_poc/start', type='json', auth='user')
    def start(
        self,
        pos_config_id,
        payment_method_id,
        amount,
        currency,
        order_uid,
        payment_line_uuid,
        payment_id=None,
        payment_client_id=None,
    ):
        diag = self._diag_context()
        _logger.info(
            "CardPointe start user=%s pos_config_id=%s payment_method_id=%s payment_id=%s payment_client_id=%s amount=%s currency=%s "
            "order_uid=%s line=%s pid=%s thread_id=%s active_count=%s",
            request.env.user.id,
            pos_config_id,
            payment_method_id,
            payment_id,
            payment_client_id,
            amount,
            currency,
            order_uid,
            payment_line_uuid,
            diag['pid'],
            diag['thread_id'],
            self._active_request_count(),
        )

        _payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error
        order = request.env['pos.order'].sudo().search([('uuid', '=', order_uid)], limit=1)
        if not order:
            return {'status': 'error', 'message': 'POS order not found for terminal request.'}
        if (
            order.cardpointe_active_request_state in ('ready', 'auth_started', 'cancel_requested')
            and order.cardpointe_active_session_key
        ):
            if order.cardpointe_active_payment_line_uuid == payment_line_uuid:
                return {
                    'status': 'in_use',
                    'message': 'This payment line already has an active CardPointe terminal session.',
                }
            return {
                'status': 'in_use',
                'message': 'Another CardPointe terminal request is already active on this order.',
            }

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
        order.write({
            'cardpointe_active_request_id': request_id,
            'cardpointe_active_session_key': connect_result['session_key'],
            'cardpointe_active_request_state': 'ready',
            'cardpointe_active_request_uid': request.env.user.id,
            'cardpointe_active_started_at': fields.Datetime.now(),
            'cardpointe_active_payment_line_uuid': payment_line_uuid,
            'cardpointe_active_payment_method_id': int(payment_method_id),
        })
        diag = self._diag_context()
        _logger.info(
            "CardPointe terminal session established request_id=%s payment_method_id=%s order_uid=%s payment_line_uuid=%s "
            "pid=%s thread_id=%s active_count=%s",
            request_id,
            payment_method_id,
            order_uid,
            payment_line_uuid,
            diag['pid'],
            diag['thread_id'],
            self._active_request_count(),
        )
        return {'status': 'ready', 'request_id': request_id}

    @http.route('/pos_cardpointe_poc/auth', type='json', auth='user')
    def auth(self, request_id, amount=None):
        order = request.env['pos.order'].sudo().cardpointe_get_order_for_request(request_id)
        if not order:
            diag = self._diag_context()
            _logger.warning(
                "CardPointe auth with unknown request_id=%s user=%s pid=%s thread_id=%s active_count=%s",
                request_id,
                request.env.user.id,
                diag['pid'],
                diag['thread_id'],
                self._active_request_count(),
            )
            return {
                'status': 'error',
                'message': 'Card terminal session expired. Start payment again.',
            }

        if order.cardpointe_active_request_uid.id != request.env.uid:
            diag = self._diag_context()
            _logger.warning(
                "CardPointe auth access denied request_id=%s expected_uid=%s got_uid=%s "
                "pid=%s thread_id=%s active_count=%s",
                request_id,
                order.cardpointe_active_request_uid.id,
                request.env.uid,
                diag['pid'],
                diag['thread_id'],
                self._active_request_count(),
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = order.cardpointe_active_payment_method_id
        if not payment_method:
            return {'status': 'error', 'message': 'Payment method no longer available.'}
        config = payment_method.cardpointe_config_id
        if not config:
            return {'status': 'error', 'message': 'CardPointe config missing on payment method.'}

        session_key = order.cardpointe_active_session_key
        order.write({'cardpointe_active_request_state': 'auth_started'})
        diag = self._diag_context()
        _logger.info(
            "CardPointe auth started request_id=%s pid=%s thread_id=%s active_count=%s "
            "config_id=%s payment_method_id=%s uid=%s order_uid=%s payment_line_uuid=%s",
            request_id,
            diag['pid'],
            diag['thread_id'],
            self._active_request_count(),
            config.id,
            payment_method.id,
            order.cardpointe_active_request_uid.id,
            order.uuid,
            order.cardpointe_active_payment_line_uuid,
        )
        terminal_client = CardPointeTerminalClient(config)

        signature_required_pre_auth = self._signature_required_pre_auth(config, amount)

        try:
            result = terminal_client.auth_card_with_session(
                amount_dollars=amount,
                order_id=order.uuid,
                session_key=session_key,
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
                    read_sig_result = terminal_client.read_signature(session_key)
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
            diag = self._diag_context()
            _logger.info(
                "CardPointe auth cleanup starting request_id=%s order_uid=%s payment_line_uuid=%s payment_method_id=%s pid=%s thread_id=%s active_count=%s",
                request_id,
                order.uuid,
                order.cardpointe_active_payment_line_uuid,
                payment_method.id,
                diag['pid'],
                diag['thread_id'],
                self._active_request_count(),
            )
            if session_key:
                disconnect_result = terminal_client.disconnect(session_key)
                if not disconnect_result.get('ok'):
                    _logger.warning(
                        "CardPointe auth cleanup disconnect failed request_id=%s order_uid=%s payment_line_uuid=%s payment_method_id=%s reason=%s pid=%s thread_id=%s",
                        request_id,
                        order.uuid,
                        order.cardpointe_active_payment_line_uuid,
                        payment_method.id,
                        disconnect_result.get('message'),
                        diag['pid'],
                        diag['thread_id'],
                    )
                    order.write({
                        'cardpointe_active_request_state': 'error',
                        'cardpointe_active_session_key': False,
                    })
                else:
                    _logger.info(
                        "CardPointe auth cleanup disconnect ok request_id=%s order_uid=%s payment_line_uuid=%s payment_method_id=%s pid=%s thread_id=%s",
                        request_id,
                        order.uuid,
                        order.cardpointe_active_payment_line_uuid,
                        payment_method.id,
                        diag['pid'],
                        diag['thread_id'],
                    )
                    order.cardpointe_clear_active_request()
            else:
                order.cardpointe_clear_active_request()

    @http.route('/pos_cardpointe_poc/cancel', type='json', auth='user')
    def cancel(self, request_id):
        order = request.env['pos.order'].sudo().cardpointe_get_order_for_request(request_id)
        if not order:
            diag = self._diag_context()
            _logger.warning(
                "CardPointe cancel with unknown request_id=%s user=%s pid=%s thread_id=%s active_count=%s",
                request_id,
                request.env.user.id,
                diag['pid'],
                diag['thread_id'],
                self._active_request_count(),
            )
            return {
                'status': 'error',
                'message': 'No active CardPointe terminal request found to cancel.',
            }

        if order.cardpointe_active_request_uid.id != request.env.uid:
            diag = self._diag_context()
            _logger.warning(
                "CardPointe cancel access denied request_id=%s expected_uid=%s got_uid=%s "
                "pid=%s thread_id=%s active_count=%s",
                request_id,
                order.cardpointe_active_request_uid.id,
                request.env.uid,
                diag['pid'],
                diag['thread_id'],
                self._active_request_count(),
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        diag = self._diag_context()
        _logger.info(
            "CardPointe cancel started request_id=%s pid=%s thread_id=%s active_count=%s "
            "config_id=%s payment_method_id=%s uid=%s order_uid=%s payment_line_uuid=%s",
            request_id,
            diag['pid'],
            diag['thread_id'],
            self._active_request_count(),
            order.cardpointe_active_payment_method_id.cardpointe_config_id.id,
            order.cardpointe_active_payment_method_id.id,
            order.cardpointe_active_request_uid.id,
            order.uuid,
            order.cardpointe_active_payment_line_uuid,
        )

        payment_method = order.cardpointe_active_payment_method_id
        if not payment_method or not payment_method.cardpointe_config_id:
            return {'status': 'error', 'message': 'CardPointe config no longer available for cancellation.'}

        session_key = order.cardpointe_active_session_key
        order.write({'cardpointe_active_request_state': 'cancel_requested'})
        terminal_client = CardPointeTerminalClient(payment_method.cardpointe_config_id)
        result = terminal_client.cancel(session_key)
        disconnect_result = terminal_client.disconnect(session_key)
        if not disconnect_result.get('ok'):
            _logger.warning(
                "CardPointe cancel cleanup disconnect failed request_id=%s order_uid=%s payment_line_uuid=%s payment_method_id=%s reason=%s pid=%s thread_id=%s",
                request_id,
                order.uuid,
                order.cardpointe_active_payment_line_uuid,
                payment_method.id,
                disconnect_result.get('message'),
                diag['pid'],
                diag['thread_id'],
            )
        else:
            _logger.info(
                "CardPointe cancel cleanup disconnect ok request_id=%s order_uid=%s payment_line_uuid=%s payment_method_id=%s pid=%s thread_id=%s",
                request_id,
                order.uuid,
                order.cardpointe_active_payment_line_uuid,
                payment_method.id,
                diag['pid'],
                diag['thread_id'],
            )
        order.cardpointe_clear_active_request()

        if result.get('ok'):
            return {
                'status': 'cancelled',
                'message': result.get('message') or 'Cancel accepted by terminal.',
                'respcode': result.get('respcode'),
                'resptext': result.get('resptext'),
            }

        _logger.warning(
            "CardPointe cancel failed request_id=%s message=%s respcode=%s resptext=%s pid=%s thread_id=%s",
            request_id,
            result.get('message'),
            result.get('respcode'),
            result.get('resptext'),
            diag['pid'],
            diag['thread_id'],
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
