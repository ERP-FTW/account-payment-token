import logging
import os
import threading
import uuid

from odoo import fields, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.payment_cardpointe_base.services.gateway import CardPointeGatewayClient
from odoo.addons.payment_cardpointe_base.services.money import format_gateway_amount

from ..services.cardpointe_terminal import CardPointeTerminalClient
from ..services.terminal_recovery import new_terminal_order_id, resolve_terminal_outcome
from ..services.signature_policy import (
    amount_meets_threshold,
    emv_indicates_signature_applicable,
    parse_emv_tag_data,
)

_logger = logging.getLogger(__name__)


class PosCardPointeController(http.Controller):
    def _diag_context(self):
        return {'pid': os.getpid(), 'thread_id': threading.get_ident()}

    def _validate_start_payload(self, pos_config_id, payment_method_id):
        pos_config = request.env['pos.config'].browse(int(pos_config_id)).exists()
        if not pos_config:
            return None, None, {'status': 'error', 'message': 'POS config not found.'}
        if pos_config.company_id not in request.env.user.company_ids:
            return None, None, {'status': 'error', 'message': 'Access denied for this POS config.'}

        payment_method, config, error = self._validate_payment_method(payment_method_id)
        if error:
            return None, None, error
        if payment_method.company_id and payment_method.company_id != pos_config.company_id:
            return None, None, {'status': 'error', 'message': 'Payment method belongs to another company.'}
        return payment_method, config, None

    def _validate_payment_method(self, payment_method_id):
        """Check that the caller may take CardPointe payments with this method.

        The payment method, its terminal configuration and the terminal's merchant configuration
        must all belong to one company the caller works in.
        """
        if not request.env.user.has_group('point_of_sale.group_pos_user'):
            return None, None, {'status': 'error', 'message': 'Access denied.'}
        payment_method = request.env['pos.payment.method'].browse(int(payment_method_id)).exists()
        if not payment_method or payment_method.use_payment_terminal != 'cardpointe_poc':
            return None, None, {'status': 'error', 'message': 'Invalid payment method.'}
        company = payment_method.company_id
        if company and company not in request.env.user.company_ids:
            return None, None, {'status': 'error', 'message': 'Access denied for this payment method.'}

        config = payment_method.cardpointe_config_id
        if not config:
            return None, None, {'status': 'error', 'message': 'CardPointe config missing on payment method.'}
        merchant = config.sudo().merchant_config_id
        if company and (config.company_id != company or (merchant and merchant.company_id != company)):
            return None, None, {
                'status': 'error',
                'message': 'CardPointe terminal or merchant configuration belongs to another company.',
            }
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
        merchant_config = self._resolve_merchant_config(terminal_config).sudo()
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
    def manual_auth(
        self,
        pos_config_id,
        payment_method_id,
        amount,
        currency,
        order_uid,
        payment_line_uuid,
        token,
        ecomind='E',
        partner_id=None,
        fallback_reason=None,
        terminal_error_status=None,
        terminal_error_message=None,
        cardholder_name=None,
        billing_address=None,
    ):
        payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error
        if not payment_method.cardpointe_manual_entry_enabled:
            return {'status': 'error', 'message': 'Manual Entry is disabled for this payment method.'}
        if not token:
            return {'status': 'error', 'message': 'Missing CardPointe token for manual entry.'}

        merchant_config = self._resolve_merchant_config(config).sudo()
        if not merchant_config:
            return {'status': 'error', 'message': 'CardPointe merchant config missing on terminal config.'}
        if not merchant_config.gateway_username or not merchant_config.gateway_password:
            return {'status': 'error', 'message': 'CardPointe gateway credentials are missing on merchant config.'}

        ecomind = (ecomind or 'E').strip().upper()
        if ecomind not in ('E', 'T'):
            ecomind = 'E'

        _logger.info(
            '[CARDPOINTE POS MANUAL] user=%s pos_config_id=%s payment_method_id=%s amount=%s order_uid=%s line=%s token_present=%s ecomind=%s fallback_reason=%s',
            request.env.user.id,
            pos_config_id,
            payment_method_id,
            amount,
            order_uid,
            payment_line_uuid,
            bool(token),
            ecomind,
            fallback_reason or '',
        )

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
            payload.update({
                k: v
                for k, v in billing_address.items()
                if k in {'address', 'city', 'region', 'country', 'postal', 'name'} and v
            })

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
        **kwargs
    ):
        diag = self._diag_context()
        _logger.info(
            'CardPointe start user=%s pos_config_id=%s payment_method_id=%s amount=%s currency=%s order_uid=%s line=%s payment_id_present=%s payment_client_id_present=%s pid=%s thread_id=%s',
            request.env.user.id,
            pos_config_id,
            payment_method_id,
            amount,
            currency,
            order_uid,
            payment_line_uuid,
            bool(payment_id),
            bool(payment_client_id),
            diag['pid'],
            diag['thread_id'],
        )
        _payment_method, config, error = self._validate_start_payload(pos_config_id, payment_method_id)
        if error:
            return error
        # The runtime lock lives on the terminal configuration, which cashiers may read but not
        # write: maintain it as superuser once the caller has been authorized above.
        config = config.sudo()

        if (
            config.cardpointe_active_request_state in ('ready', 'auth_started', 'cancel_requested')
            and config.cardpointe_active_session_key
        ):
            previous_request_id = config.cardpointe_active_request_id
            stale_cleared = config.cardpointe_clear_stale_active_request()
            if stale_cleared:
                _logger.warning(
                    'CardPointe start recovered stale runtime lock config_id=%s old_request_id=%s',
                    config.id,
                    previous_request_id,
                )
            else:
                _logger.warning(
                    'CardPointe start blocked by active request config_id=%s request_id=%s payment_method_id=%s order_uid=%s payment_line_uuid=%s',
                    config.id,
                    config.cardpointe_active_request_id,
                    payment_method_id,
                    order_uid,
                    payment_line_uuid,
                )
                return {
                    'status': 'in_use',
                    'message': 'This payment line already has an active CardPointe terminal session.'
                    if config.cardpointe_active_payment_line_uuid == payment_line_uuid
                    else 'Terminal is already handling another CardPointe request.',
                }

        connect_result = CardPointeTerminalClient(config).connect()
        if not connect_result.get('ok'):
            _logger.warning(
                'CardPointe start failed to connect user=%s payment_method_id=%s reason=%s',
                request.env.user.id,
                payment_method_id,
                connect_result.get('message'),
            )
            return {
                'status': connect_result.get('status', 'error'),
                'message': connect_result.get('message') or 'Unable to connect terminal session.',
            }

        request_id = str(uuid.uuid4())
        terminal_order_id = new_terminal_order_id()
        config.write({
            'cardpointe_active_request_id': request_id,
            'cardpointe_active_terminal_order_id': terminal_order_id,
            'cardpointe_active_session_key': connect_result['session_key'],
            'cardpointe_active_request_state': 'ready',
            'cardpointe_active_request_uid': request.env.user.id,
            'cardpointe_active_started_at': fields.Datetime.now(),
            'cardpointe_active_order_uid': order_uid,
            'cardpointe_active_payment_line_uuid': payment_line_uuid,
            'cardpointe_active_payment_method_id': int(payment_method_id),
        })
        _logger.info(
            'CardPointe terminal session established request_id=%s config_id=%s payment_method_id=%s order_uid=%s payment_line_uuid=%s pid=%s thread_id=%s',
            request_id,
            config.id,
            payment_method_id,
            order_uid,
            payment_line_uuid,
            diag['pid'],
            diag['thread_id'],
        )
        return {'status': 'ready', 'request_id': request_id, 'terminal_order_id': terminal_order_id}

    @http.route('/pos_cardpointe_poc/auth', type='json', auth='user')
    def auth(self, request_id, amount=None):
        config = request.env['pos.cardpointe.terminal.config'].sudo().cardpointe_get_config_for_request(request_id)
        if not config:
            diag = self._diag_context()
            _logger.warning(
                'CardPointe auth with unknown request_id=%s user=%s pid=%s thread_id=%s',
                request_id,
                request.env.user.id,
                diag['pid'],
                diag['thread_id'],
            )
            return {'status': 'error', 'message': 'Card terminal session expired. Start payment again.'}

        if config.cardpointe_active_request_uid.id != request.env.uid:
            _logger.warning(
                'CardPointe auth access denied request_id=%s expected_uid=%s got_uid=%s',
                request_id,
                config.cardpointe_active_request_uid.id,
                request.env.uid,
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = config.cardpointe_active_payment_method_id
        if not payment_method:
            return {'status': 'error', 'message': 'Payment method no longer available.'}
        if not payment_method.cardpointe_config_id:
            return {'status': 'error', 'message': 'CardPointe config missing on payment method.'}

        session_key = config.cardpointe_active_session_key
        config.write({'cardpointe_active_request_state': 'auth_started'})
        terminal_client = CardPointeTerminalClient(config)

        signature_required_pre_auth = self._signature_required_pre_auth(config, amount)
        terminal_order_id = self._terminal_order_id(config)
        try:
            try:
                result = terminal_client.auth_card_with_session(
                    amount_dollars=amount,
                    order_id=terminal_order_id,
                    session_key=session_key,
                    include_signature=signature_required_pre_auth,
                )
            except Exception as exc:  # noqa: BLE001 - the request may have reached the terminal
                _logger.exception('CardPointe authCard raised request_id=%s order_id=%s', request_id, terminal_order_id)
                result = {'ok': False, 'status': 'timeout', 'message': str(exc)}
            result = self._resolve_if_unknown(config, result, terminal_order_id, amount)

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
                            'CardPointe on_policy readSignature failed request_id=%s reason=%s',
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
                                    'CardPointe on_policy sigcap failed request_id=%s reason=%s',
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
                    'terminal_order_id': terminal_order_id,
                    'recovered': bool(result.get('recovered')),
                }

            return {
                'status': result.get('status', 'error'),
                'message': result.get('message') or result.get('resptext') or 'Terminal payment failed.',
                'respcode': result.get('respcode'),
                'resptext': result.get('resptext'),
                'terminal_order_id': terminal_order_id,
                'resolution': result.get('resolution'),
            }
        finally:
            disconnect_result = terminal_client.disconnect(session_key) if session_key else {'ok': True}
            if not disconnect_result.get('ok'):
                _logger.warning(
                    'CardPointe auth cleanup disconnect failed request_id=%s reason=%s',
                    request_id,
                    disconnect_result.get('message'),
                )
            config.cardpointe_clear_active_request()

    @http.route('/pos_cardpointe_poc/cancel', type='json', auth='user')
    def cancel(self, request_id):
        config = request.env['pos.cardpointe.terminal.config'].sudo().cardpointe_get_config_for_request(request_id)
        if not config:
            diag = self._diag_context()
            _logger.warning(
                'CardPointe cancel with unknown request_id=%s user=%s pid=%s thread_id=%s',
                request_id,
                request.env.user.id,
                diag['pid'],
                diag['thread_id'],
            )
            return {'status': 'error', 'message': 'No active CardPointe terminal request found to cancel.'}

        if config.cardpointe_active_request_uid.id != request.env.uid:
            _logger.warning(
                'CardPointe cancel access denied request_id=%s expected_uid=%s got_uid=%s',
                request_id,
                config.cardpointe_active_request_uid.id,
                request.env.uid,
            )
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = config.cardpointe_active_payment_method_id
        if not payment_method or not payment_method.cardpointe_config_id:
            return {'status': 'error', 'message': 'CardPointe config no longer available for cancellation.'}

        session_key = config.cardpointe_active_session_key
        config.write({'cardpointe_active_request_state': 'cancel_requested'})
        terminal_client = CardPointeTerminalClient(payment_method.cardpointe_config_id)
        result = terminal_client.cancel(session_key)
        disconnect_result = terminal_client.disconnect(session_key)
        if not disconnect_result.get('ok'):
            _logger.warning(
                'CardPointe cancel cleanup disconnect failed request_id=%s config_id=%s payment_method_id=%s reason=%s',
                request_id,
                config.id,
                payment_method.id,
                disconnect_result.get('message'),
            )
        config.cardpointe_clear_active_request()

        if result.get('ok'):
            return {
                'status': 'cancelled',
                'message': result.get('message') or 'Cancel accepted by terminal.',
                'respcode': result.get('respcode'),
                'resptext': result.get('resptext'),
            }
        return {
            'status': 'error',
            'message': result.get('message') or 'Cancel failed.',
            'respcode': result.get('respcode'),
            'resptext': result.get('resptext'),
        }

    @http.route('/pos_cardpointe_poc/refund', type='json', auth='user')
    def refund(self, payment_method_id, amount, refunded_orderline_ids):
        payment_method, _config, error = self._validate_payment_method(payment_method_id)
        if error:
            return error

        try:
            result = request.env['pos.payment'].cardpointe_process_refund(
                payment_method_id=payment_method.id,
                amount=amount,
                refunded_orderline_ids=refunded_orderline_ids or [],
            )
        except UserError as exc:
            message = exc.args[0] if getattr(exc, 'args', None) else str(exc)
            _logger.warning('CardPointe refund failed payment_method_id=%s reason=%s', payment_method.id, message)
            return {'status': 'error', 'message': message}

        return {
            'status': result.get('status', 'error'),
            'message': result.get('message'),
            'retref': result.get('retref'),
            'respcode': result.get('respcode'),
            'resptext': result.get('resptext'),
            'operation': result.get('operation'),
            'original_retref': result.get('original_retref'),
            'ok': result.get('ok', result.get('status') == 'approved'),
        }

    # --- Unknown terminal outcomes -------------------------------------------------------------

    @staticmethod
    def _terminal_order_id(config):
        return config.cardpointe_active_terminal_order_id or config.cardpointe_active_order_uid

    @staticmethod
    def _needs_resolution(result):
        """True when the terminal call may have charged the card without telling us."""
        # `timeout` covers a read timeout and a 5xx from the terminal service; a transport error
        # means no HTTP answer at all. In each case the authorization may have gone through.
        return result.get('status') == 'timeout' or bool(result.get('transport_error'))

    def _resolve_if_unknown(self, terminal_config, result, terminal_order_id, amount):
        if not self._needs_resolution(result):
            return result
        _logger.warning(
            'CardPointe authCard outcome unknown order_id=%s status=%s; inquiring by order id',
            terminal_order_id, result.get('status'),
        )
        return self._resolve_terminal_order(terminal_config, terminal_order_id, amount)

    def _resolve_terminal_order(self, terminal_config, terminal_order_id, amount):
        """Resolve through the Gateway's documented inquiry by order id. Never charges."""
        merchant_config = self._resolve_merchant_config(terminal_config).sudo()
        if not merchant_config or not merchant_config.gateway_username or not merchant_config.gateway_password:
            return {
                'status': 'unknown',
                'message': 'Payment outcome unknown and CardPointe gateway credentials are missing; '
                           'check CardPointe reporting before taking another payment.',
            }
        outcome = resolve_terminal_outcome(
            CardPointeGatewayClient(merchant_config), merchant_config.mid, terminal_order_id, amount,
        )
        status = outcome.get('status')
        if status == 'approved' and not outcome.get('amount_matches'):
            # A charge stands, but not for this line's amount: accepting it would under- or overpay the
            # order. Keep the line unresolved until the charge is reconciled in CardPointe.
            _logger.warning('CardPointe recovered approval amount differs order_id=%s amount=%s requested=%s',
                            terminal_order_id, outcome.get('amount'), amount)
            return {
                'ok': False,
                'status': 'unknown',
                'resolution': 'amount_mismatch',
                'retref': outcome.get('retref'),
                'amount': outcome.get('amount'),
                'message': f"CardPointe shows an approval of {outcome.get('amount')} (reference "
                           f"{outcome.get('retref')}) for this payment, but {amount} was requested. Do not take "
                           'another payment for this line: void or adjust that charge in CardPointe first, then '
                           'press Send to check again.',
            }
        if status == 'approved':
            return dict(outcome, ok=True)
        if status in ('not_found', 'voided'):
            return {
                'ok': False,
                'status': 'error',
                'message': 'The terminal did not complete the payment and no charge stands '
                           f'(CardPointe: {status}). You can take the payment again.',
                'resolution': status,
            }
        if status == 'declined':
            return {
                'ok': False, 'status': 'declined', 'message': outcome.get('message') or 'Declined.',
                'respcode': outcome.get('respcode'), 'resptext': outcome.get('resptext'),
            }
        return {
            'ok': False,
            'status': 'unknown',
            'message': 'Payment outcome unknown: the card may have been charged. Do not take another '
                       'payment for this line; use "Check status" once the terminal and gateway are reachable.',
        }

    @http.route('/pos_cardpointe_poc/inquire', type='json', auth='user')
    def inquire(self, payment_method_id, terminal_order_id, amount=None):
        """Resolve a payment line left in the unknown state, without charging."""
        _payment_method, config, error = self._validate_payment_method(payment_method_id)
        if error:
            return error
        if not terminal_order_id:
            return {'status': 'error', 'message': 'Missing terminal order id.'}
        result = self._resolve_terminal_order(config, terminal_order_id, amount)
        result['terminal_order_id'] = terminal_order_id
        result.pop('ok', None)
        return result

    @http.route('/pos_cardpointe_poc/poll', type='json', auth='user')
    def poll(self, payment_method_id, request_id=None, terminal_order_id=None, amount=None):
        if terminal_order_id:
            return self.inquire(payment_method_id, terminal_order_id, amount)
        return {
            'status': 'error',
            'message': 'Provide the terminal order id of the payment to resolve.',
        }
