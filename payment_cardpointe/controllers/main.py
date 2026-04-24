# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)


class CardPointeController(http.Controller):

    def _cardpointe_is_truthy(self, value):
        return str(value).strip().lower() in ('1', 'true', 't', 'yes', 'y', 'on')

    @http.route('/payment/cardpointe/process', type='json', auth='public')
    def cardpointe_process(self, **kwargs):
        """Process a CardPointe website payment or tokenization request."""
        reference = kwargs.get('reference')
        access_token = kwargs.get('access_token')
        token = kwargs.get('token')
        partner_id = kwargs.get('partner_id')
        meta = kwargs.get('meta')
        save_token = self._cardpointe_is_truthy(kwargs.get('save_token'))
        flow = kwargs.get('flow')

        missing_fields = []
        if not reference:
            missing_fields.append('reference')
        if not access_token:
            missing_fields.append('access_token')
        if not token:
            missing_fields.append('token')
        if missing_fields:
            _logger.warning(
                "[CARDPOINTE] Missing required payment data: %s",
                ", ".join(missing_fields),
            )
            raise ValidationError("CardPointe: " + _("Missing required payment data."))

        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference)
        ], limit=1)
        if not tx_sudo:
            raise ValidationError("CardPointe: " + _("Transaction not found."))

        if hasattr(tx_sudo, '_cardpointe_set_default_stored_credential_semantics'):
            tx_sudo._cardpointe_set_default_stored_credential_semantics(
                initiator='customer', schedule='unscheduled'
            )

        try:
            payload_partner_id = int(partner_id) if partner_id is not None else None
        except (TypeError, ValueError):
            payload_partner_id = None
        if payload_partner_id and payload_partner_id != tx_sudo.partner_id.id:
            _logger.warning(
                "[CARDPOINTE] partner mismatch tx_ref=%s payload_partner_id=%s tx_partner_id=%s",
                reference,
                payload_partner_id,
                tx_sudo.partner_id.id,
            )

        if not payment_utils.check_access_token(
            access_token,
            tx_sudo.partner_id.id,
            tx_sudo.amount,
            tx_sudo.currency_id.id,
        ):
            _logger.warning(
                "[CARDPOINTE] tampered payment request tx_ref=%s partner_id=%s amount=%s currency_id=%s",
                tx_sudo.reference,
                tx_sudo.partner_id.id,
                tx_sudo.amount,
                tx_sudo.currency_id.id,
            )
            raise ValidationError("CardPointe: " + _("Received tampered payment request data."))

        is_validation = (
            getattr(tx_sudo, 'operation', False) == 'validation'
            or not tx_sudo.amount
        )

        effective_save_payment_method = bool(is_validation or save_token)

        _logger.info(
            "[CARDPOINTE] process request received tx_ref=%s partner_id=%s token_present=%s "
            "save_token=%s is_validation=%s effective_save_payment_method=%s",
            reference,
            partner_id,
            bool(token),
            save_token,
            is_validation,
            effective_save_payment_method,
        )

        if is_validation:
            result = tx_sudo._cardpointe_tokenize_from_token(token, meta=meta)
        elif save_token:
            result = tx_sudo._cardpointe_charge_and_tokenize_from_token(token, meta=meta, flow=flow)
        else:
            result = tx_sudo._cardpointe_charge_from_token(token, meta=meta, flow=flow)

        if not result.get('ok'):
            _logger.warning(
                "[CARDPOINTE] process failure tx_ref=%s message=%s",
                reference,
                result.get('message'),
            )

        _logger.info(
            "[CARDPOINTE] process result tx_ref=%s success=%s",
            reference, result.get('ok'),
        )
        return {
            'success': bool(result.get('ok')),
            'redirect_url': '/payment/status',
            'message': result.get('message'),
        }
