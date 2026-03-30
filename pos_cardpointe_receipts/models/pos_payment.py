# -*- coding: utf-8 -*-
import logging

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

    def _cardpointe_reprint_precheck(self):
        self.ensure_one()

        if not self.cardpointe_is_terminal_payment:
            raise UserError(_('Receipt reprint is only available for CardPointe terminal payments.'))

        if not self.cardpointe_ok or self.cardpointe_status != 'approved':
            raise UserError(_('Receipt reprint is only available for approved CardPointe payments.'))

        payment_method = self.payment_method_id
        terminal_config = payment_method.cardpointe_config_id
        if not terminal_config:
            raise UserError(_('CardPointe terminal configuration is missing on this payment method.'))

        if not terminal_config.device_serial:
            raise UserError(_('CardPointe terminal HSN is missing on the terminal configuration.'))

        return terminal_config

    def _get_cardpointe_receipt_order_id(self):
        self.ensure_one()

        order = self.pos_order_id
        if not order:
            raise UserError(_('This payment is not linked to a POS order.'))

        candidates = [
            (order.pos_reference or '').strip(),
            (order.name or '').strip(),
        ]

        for candidate in candidates:
            if candidate.startswith('Order '):
                candidate = candidate[len('Order '):].strip()
            if candidate:
                return candidate

        raise UserError(_('No POS order reference was found for receipt reprint.'))

    def action_cardpointe_reprint_receipt(self):
        self.ensure_one()

        terminal_config = self._cardpointe_reprint_precheck()
        order_id = self._get_cardpointe_receipt_order_id()

        _logger.info(
            "CardPointe receipt reprint payment_id=%s pos_reference=%s order_name=%s resolved_order_id=%s retref=%s",
            self.id,
            self.pos_order_id.pos_reference if self.pos_order_id else False,
            self.pos_order_id.name if self.pos_order_id else False,
            order_id,
            self.cardpointe_retref,
        )

        service = CardPointeTerminalReceiptService(terminal_config)
        result = service.reprint_receipt(order_id=order_id)

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