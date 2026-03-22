import logging

from odoo import http
from odoo.http import request

from odoo.addons.pos_cardpointe_poc.controllers.main import PosCardPointeController
from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

_logger = logging.getLogger(__name__)


class PosCardPointeTippingController(PosCardPointeController):

    @http.route('/pos_cardpointe_poc/start', type='json', auth='user')
    def start(self, pos_config_id, payment_method_id, amount, currency, order_uid, payment_line_uuid):
        result = super().start(pos_config_id, payment_method_id, amount, currency, order_uid, payment_line_uuid)
        if result.get('status') != 'ready' or not result.get('request_id'):
            return result

        active_request = self._get_active_request(result['request_id'])
        if not active_request:
            return result

        payment_method = request.env['pos.payment.method'].browse(active_request['payment_method_id']).exists()
        config = payment_method.cardpointe_config_id if payment_method else False
        result['tip_enabled'] = bool(config and config.enable_tips)
        return result

    @http.route('/pos_cardpointe_poc/auth', type='json', auth='user')
    def auth(self, request_id):
        active_request = self._get_active_request(request_id)
        if not active_request:
            _logger.warning('CardPointe auth with unknown request_id=%s user=%s', request_id, request.env.user.id)
            return {
                'status': 'error',
                'message': 'Card terminal session expired. Start payment again.',
            }

        if active_request['uid'] != request.env.uid:
            _logger.warning(
                'CardPointe auth access denied request_id=%s expected_uid=%s got_uid=%s',
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

        _logger.info('CardPointe auth started request_id=%s', request_id)
        terminal_client = CardPointeTerminalClient(config)

        base_amount = float(active_request['amount'] or 0.0)
        tip_amount = 0.0
        total_amount = base_amount
        signature_required_pre_auth = self._signature_required_pre_auth(config, base_amount)

        try:
            if config.enable_tips:
                tip_result = terminal_client.tip_with_session(
                    session_key=active_request['session_key'],
                    amount_dollars=base_amount,
                    prompt='Select tip amount',
                )
                if not tip_result.get('ok'):
                    return {
                        'status': tip_result.get('status', 'error'),
                        'message': tip_result.get('message') or 'Tip selection failed.',
                        'respcode': tip_result.get('respcode'),
                        'resptext': tip_result.get('resptext'),
                    }
                tip_amount = float(tip_result.get('tip_amount') or 0.0)
                total_amount = base_amount + tip_amount

            result = terminal_client.auth_card_with_session(
                amount_dollars=total_amount,
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
                    'signature_required': signature_required,
                    'signature_captured': signature_captured,
                    'signature_method': signature_method,
                    'cardpointe_tip_amount': tip_amount,
                    'cardpointe_base_amount': base_amount,
                    'cardpointe_total_amount': total_amount,
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
                    'CardPointe auth cleanup disconnect failed request_id=%s reason=%s',
                    request_id,
                    disconnect_result.get('message'),
                )
