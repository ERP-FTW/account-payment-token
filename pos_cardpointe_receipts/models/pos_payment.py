import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.pos_cardpointe_receipts.services.cardpointe_receipt import CardPointeTerminalReceiptService

_logger = logging.getLogger(__name__)


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    cardpointe_reprint_attempt_count = fields.Integer(
        string='CardPointe Reprint Attempts',
        default=0,
        readonly=True,
        copy=False,
    )

    def _is_cardpointe_terminal_payment(self):
        self.ensure_one()
        payment_method = self.payment_method_id
        return bool(payment_method and payment_method.use_payment_terminal == 'cardpointe_poc')

    def _cardpointe_reprint_precheck(self):
        self.ensure_one()

        if not self._is_cardpointe_terminal_payment():
            raise UserError(_('Receipt reprint is only available for CardPointe terminal payments.'))

        if not self.cardpointe_ok or self.cardpointe_status != 'approved':
            raise UserError(_('Receipt reprint is only available for approved CardPointe payments.'))

        if not self.cardpointe_retref:
            raise UserError(_('Missing CardPointe reference (retref); cannot reprint receipt.'))

        terminal_config = self.payment_method_id.cardpointe_config_id
        if not terminal_config:
            raise UserError(_('CardPointe terminal configuration is missing on this payment method.'))

        if not terminal_config.device_serial:
            raise UserError(_('CardPointe terminal HSN is missing on the terminal configuration.'))

        return terminal_config

    def action_cardpointe_reprint_receipt(self):
        self.ensure_one()
        terminal_config = self._cardpointe_reprint_precheck()

        service = CardPointeTerminalReceiptService(terminal_config)
        order_id = self.pos_order_id.pos_reference or self.pos_order_id.name
        result = service.reprint_receipt(retref=self.cardpointe_retref, order_id=order_id)

        self.cardpointe_reprint_attempt_count += 1

        _logger.info(
            "CardPointe receipt reprint result payment_id=%s retref=%s status=%s ok=%s",
            self.id,
            self.cardpointe_retref,
            result.get('status'),
            result.get('ok'),
        )

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
