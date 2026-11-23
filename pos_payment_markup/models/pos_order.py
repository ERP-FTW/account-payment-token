from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.account_payment_markup.services.calculation import UNSET


class PosOrder(models.Model):
    _inherit = 'pos.order'


    def _payment_fields(self, order, ui_paymentline):
        values = super()._payment_fields(order, ui_paymentline)
        method = self.env['pos.payment.method'].browse(ui_paymentline.get('payment_method_id')).exists()
        markup_amount = float(ui_paymentline.get('markup_amount') or 0.0)
        base_amount = float(ui_paymentline.get('markup_base_amount') or 0.0)
        evidence = ui_paymentline.get('markup_evidence') or {}
        markup_fields_present = (
            ui_paymentline.get('markup_amount_source') in ('local', 'processor')
            or bool(evidence)
            or any(ui_paymentline.get(key) not in (None, False, 0, 0.0, '')
                   for key in ('markup_amount', 'markup_total_amount',
                               'markup_payment_reference'))
        )
        if markup_fields_present:
            if not method:
                raise UserError('POS payment markup requires a mapped payment method.')
            order_currency = order.currency_id
            if not order_currency:
                raise UserError('POS order currency is required for payment markup validation.')
            preview = method._markup_preview(
                base_amount, currency=order_currency,
                evidence_verified=bool(evidence.get('verified')),
                card_type=evidence.get('card_type'),
                processor_surcharge_present=(evidence['surcharge_present'] if 'surcharge_present' in evidence else None),
                evidence_trusted=bool(evidence.get('server_verified')),
                processor_amount=(evidence['processor_markup_amount']
                                  if 'processor_markup_amount' in evidence else UNSET),
            )
            if evidence and evidence.get('provenance') != 'cardpointe-server':
                raise UserError('POS payment markup requires a server-stamped terminal response.')
            if markup_amount < 0 or base_amount < 0:
                raise UserError('POS payment markup amounts cannot be negative.')
            submitted_source = ui_paymentline.get('markup_amount_source')
            if submitted_source != preview['source']:
                raise UserError('POS payment markup source does not match the provider calculation.')
            if preview['amount'] != order_currency.round(markup_amount):
                raise UserError('POS payment markup does not match the provider calculation.')
            if not preview['eligible']:
                raise UserError('POS payment method is not eligible for the configured markup.')
            payment_amount = order_currency.round(float(ui_paymentline.get('amount') or 0.0))
            if payment_amount != order_currency.round(preview['total']):
                raise UserError('POS payment total does not match the provider calculation.')
            submitted_total = order_currency.round(
                float(ui_paymentline.get('markup_total_amount') or 0.0))
            if submitted_total != order_currency.round(preview['total']):
                raise UserError('POS payment markup total does not match the tender.')
        values.update({
            key: ui_paymentline.get(key)
            for key in ('markup_base_amount', 'markup_rate_applied', 'markup_amount',
                        'markup_total_amount', 'markup_amount_source',
                        'markup_payment_reference')
            if key in ui_paymentline
        })
        return values

    def _markup_total_from_payments(self):
        self.ensure_one()
        return self.currency_id.round(sum(self.payment_ids.mapped('markup_amount')))


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    markup_generated = fields.Boolean(copy=False, default=False)

    def _order_line_fields(self, line, session_id=None):
        values = super()._order_line_fields(line, session_id=session_id)
        values[2]['markup_generated'] = bool(line[2].get('markup_generated'))
        return values
