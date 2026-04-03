import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.pos_cardpointe_receipts.services.cardpointe_receipt import CardPointeTerminalReceiptService

_logger = logging.getLogger(__name__)


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    cardpointe_is_terminal_payment = fields.Boolean(
        compute='_compute_cardpointe_is_terminal_payment',
        string='Is CardPointe Terminal Payment',
    )
    cardpointe_reprint_attempt_count = fields.Integer(
        string='CardPointe Reprint Attempts',
        default=0,
        readonly=True,
        copy=False,
    )

    def _compute_cardpointe_is_terminal_payment(self):
        for payment in self:
            payment.cardpointe_is_terminal_payment = bool(
                payment.payment_method_id
                and payment.payment_method_id.use_payment_terminal == 'cardpointe_poc'
            )

    def _get_cardpointe_receipt_order_id(self):
        self.ensure_one()

        order = self.pos_order_id
        values = [
            (order.pos_reference or '').strip() if order else '',
            (order.name or '').strip() if order else '',
        ]

        for value in values:
            cleaned = value
            if cleaned.startswith('Order '):
                cleaned = cleaned[len('Order '):].strip()
            if not cleaned:
                continue
            match = re.search(r'(\d{5}-\d{3}-\d{4})', cleaned)
            if match:
                return match.group(1)
            return cleaned

        raise UserError(_('No POS order reference was found for receipt reprint.'))

    def _cardpointe_reprint_precheck(self):
        self.ensure_one()

        if not self.pos_order_id:
            raise UserError(_('This payment is not linked to a POS order.'))

        if not self.cardpointe_is_terminal_payment:
            raise UserError(_('Receipt reprint is only available for CardPointe terminal payments.'))

        terminal_config = self.payment_method_id.cardpointe_config_id
        if not terminal_config:
            raise UserError(_('CardPointe terminal configuration is missing on this payment method.'))

        return terminal_config

    def action_cardpointe_reprint_receipt(self):
        self.ensure_one()

        terminal_config = self._cardpointe_reprint_precheck()
        order_id = self._get_cardpointe_receipt_order_id()

        _logger.info(
            'CardPointe receipt reprint payment_id=%s pos_reference=%s order_name=%s resolved_order_id=%s',
            self.id,
            self.pos_order_id.pos_reference,
            self.pos_order_id.name,
            order_id,
        )

        service = CardPointeTerminalReceiptService(terminal_config)
        result = service.reprint_receipt(order_id=order_id)

        self.cardpointe_reprint_attempt_count += 1

        if not result.get('ok'):
            raise UserError(result.get('message') or _('CardPointe receipt reprint failed.'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('CardPointe Receipt Reprint'),
                'message': result.get('message') or _('Receipt reprint request sent to terminal.'),
                'type': 'success',
                'sticky': False,
            },
        }
