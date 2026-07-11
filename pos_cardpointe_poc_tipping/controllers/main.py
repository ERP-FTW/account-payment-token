import logging

from odoo import http
from odoo.http import request

from odoo.addons.pos_cardpointe_poc.controllers.main import PosCardPointeController
from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

_logger = logging.getLogger(__name__)


class PosCardPointeTippingController(PosCardPointeController):
    """Add terminal tipping while retaining the current v18 base flow."""

    @staticmethod
    def _native_tip_configuration_error(pos_config):
        if not getattr(pos_config, 'iface_tipproduct', False):
            return 'Enable Tips in the Point of Sale settings before using CardPointe terminal tipping.'
        if not getattr(pos_config, 'tip_product_id', False):
            return 'Select a Tip Product in the Point of Sale settings before using CardPointe terminal tipping.'
        return False

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
        payment_method = request.env['pos.payment.method'].browse(int(payment_method_id)).exists()
        terminal_config = payment_method.cardpointe_config_id if payment_method else False
        if terminal_config and terminal_config.enable_tips:
            pos_config = request.env['pos.config'].browse(int(pos_config_id)).exists()
            error = self._native_tip_configuration_error(pos_config)
            if error:
                return {'status': 'error', 'message': error}

        return super().start(
            pos_config_id=pos_config_id,
            payment_method_id=payment_method_id,
            amount=amount,
            currency=currency,
            order_uid=order_uid,
            payment_line_uuid=payment_line_uuid,
            payment_id=payment_id,
            payment_client_id=payment_client_id,
            **kwargs
        )

    @http.route('/pos_cardpointe_poc/auth', type='json', auth='user')
    def auth(self, request_id, amount=None):
        config = request.env['pos.cardpointe.terminal.config'].sudo().cardpointe_get_config_for_request(request_id)
        if not config:
            return {'status': 'error', 'message': 'Card terminal session expired. Start payment again.'}

        if config.cardpointe_active_request_uid.id != request.env.uid:
            return {'status': 'error', 'message': 'Access denied for this payment session.'}

        payment_method = config.cardpointe_active_payment_method_id
        if not payment_method:
            return {'status': 'error', 'message': 'Payment method no longer available.'}
        if not payment_method.cardpointe_config_id:
            return {'status': 'error', 'message': 'CardPointe config missing on payment method.'}

        try:
            base_amount = float(amount or 0.0)
        except (TypeError, ValueError):
            return {'status': 'error', 'message': 'Invalid payment amount.'}
        if base_amount <= 0:
            return {'status': 'error', 'message': 'Payment amount must be greater than zero.'}

        session_key = config.cardpointe_active_session_key
        config.write({'cardpointe_active_request_state': 'auth_started'})
        terminal_client = CardPointeTerminalClient(config)

        tip_amount = 0.0
        total_amount = base_amount
        tip_prompted = bool(config.enable_tips)
        signature_required_pre_auth = False

        try:
            if tip_prompted:
                tip_result = terminal_client.tip_with_session(
                    session_key=session_key,
                    amount_dollars=base_amount,
                    prompt=config.tip_prompt,
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

            signature_required_pre_auth = self._signature_required_pre_auth(config, total_amount)
            result = terminal_client.auth_card_with_session(
                amount_dollars=total_amount,
                order_id=config.cardpointe_active_order_uid,
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
                    if read_sig_result.get('ok'):
                        signature_captured = True
                        if result.get('retref'):
                            sigcap_result = self._attach_signature_sigcap(
                                config,
                                retref=result.get('retref'),
                                signature_blob=read_sig_result.get('signature'),
                            )
                            if not sigcap_result.get('ok'):
                                _logger.warning(
                                    'CardPointe tipping sigcap failed request_id=%s reason=%s',
                                    request_id,
                                    sigcap_result.get('message'),
                                )
                    else:
                        _logger.warning(
                            'CardPointe tipping readSignature failed request_id=%s reason=%s',
                            request_id,
                            read_sig_result.get('message'),
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
                    'cardpointe_tip_prompted': tip_prompted,
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
            disconnect_result = terminal_client.disconnect(session_key) if session_key else {'ok': True}
            if not disconnect_result.get('ok'):
                _logger.warning(
                    'CardPointe tipping disconnect failed request_id=%s reason=%s',
                    request_id,
                    disconnect_result.get('message'),
                )
            config.cardpointe_clear_active_request()
