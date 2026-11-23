from odoo import api, fields, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def write(self, values):
        if values.get('state') == 'draft' and self.filtered(lambda o: o.state in ['paid', 'done', 'invoiced'] and o.lines.filtered('markup_generated')):
            raise UserError('Finalized fee evidence cannot be reopened for editing.')
        return super().write(values)

    def _process_saved_order(self, draft):
        if not draft and self.state != 'cancel':
            self._validate_markup_evidence()
        return super()._process_saved_order(draft)

    @api.constrains('state')
    def _check_final_markup(self):
        for order in self.filtered(lambda order: order.state == 'paid'):
            order._validate_markup_evidence()

    def _validate_markup_evidence(self):
        self.ensure_one()
        currency = self.currency_id
        generated = self.lines.filtered('markup_generated')
        if generated.filtered(lambda line: line.refunded_orderline_id and not line.refunded_orderline_id.markup_generated):
            raise UserError('A refund cannot invent fee evidence on an ordinary original line.')
        originals = self.lines.mapped('refunded_orderline_id').filtered('markup_generated').sorted('id')
        for original in originals:
            self.env.cr.execute('SELECT id FROM pos_order_line WHERE id = %s FOR UPDATE', [original.id])
            lines = self.lines.filtered(lambda line: line.refunded_orderline_id == original)
            prior = self.env['pos.order.line'].search([
                ('refunded_orderline_id', '=', original.id), ('order_id', '!=', self.id),
                ('order_id.state', 'not in', ['draft', 'cancel']),
            ])
            if (original.order_id.state not in ['paid', 'done', 'invoiced'] or original.qty <= 0
                    or original.order_id.company_id != self.company_id
                    or original.order_id.currency_id != currency
                    or sum(abs(x.qty) for x in lines + prior) > original.qty):
                raise UserError('Refund exceeds remaining original fee evidence.')
            for line in lines:
                if (line.qty >= 0 or line.price_unit != original.price_unit
                        or line.discount != original.discount or line.tax_ids != original.tax_ids
                        or line.product_id != original.product_id):
                    raise UserError('Refund must retain original fee price and taxes.')
                if not line.markup_generated:
                    line.markup_generated = True
        for payment in self.payment_ids.filtered(lambda p: p.amount > 0 and not p.is_change):
            method = payment.payment_method_id
            fee_lines = generated.filtered(lambda line: line.markup_payment_reference == payment.uuid)
            if not method.markup_enabled:
                if fee_lines or payment.markup_amount:
                    raise UserError('An unconfigured tender cannot collect a fee.')
                continue
            preview = method._markup_preview(payment.markup_base_amount or payment.amount, currency=currency, fiscal_position=self.fiscal_position_id)
            if not preview['amount'] and not fee_lines and not payment.markup_amount:
                continue
            if len(fee_lines) != 1:
                raise UserError('Each configured tender requires exactly one linked fee line.')
            line = fee_lines
            if line.qty != 1 or line.discount or line.product_id != method.markup_product_id:
                raise UserError('Fee product, quantity or discount differs from the configured fee.')
            if (currency.compare_amounts(line.price_subtotal, preview['amount'])
                    or currency.compare_amounts(payment.amount, preview['total'])
                    or currency.compare_amounts(line.price_subtotal_incl, payment.amount - payment.markup_base_amount)
                    or currency.compare_amounts(payment.markup_amount, preview['amount'])):
                raise UserError('The fee disclosure, tender and native order line must agree.')
        for line in generated.filtered(lambda l: not l.refunded_orderline_id):
            if not self.payment_ids.filtered(lambda p: p.uuid == line.markup_payment_reference and p.amount > 0):
                raise UserError('Orphan fee line has no positive payment.')

    def _markup_total_from_payments(self):
        self.ensure_one()
        return self.currency_id.round(sum(self.lines.filtered('markup_generated').mapped('price_subtotal')))


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    markup_generated = fields.Boolean(copy=False, default=False)
    markup_payment_reference = fields.Char(copy=False, index=True)

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            original = self.browse(values.get('refunded_orderline_id'))
            if original.markup_generated:
                values.update(markup_generated=True, markup_payment_reference=original.markup_payment_reference)
        records = super().create(values_list)
        for order in records.mapped('order_id').filtered(lambda o: o.state in ['paid', 'done', 'invoiced']):
            order._validate_markup_evidence()
        return records

    def write(self, values):
        protected = {'markup_generated', 'markup_payment_reference', 'refunded_orderline_id',
                     'product_id', 'price_unit', 'qty', 'discount', 'tax_ids', 'order_id'}
        if protected.intersection(values) and self.filtered(
                lambda line: line.order_id.state in ['paid', 'done', 'invoiced']
                and (line.markup_generated or line.refunded_orderline_id.markup_generated)):
            raise UserError('Finalized original fee evidence is immutable.')
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda line: line.markup_generated and line.order_id.state in ['paid', 'done', 'invoiced']):
            raise UserError('Finalized original fee evidence cannot be deleted.')
        return super().unlink()

    @api.model
    def _load_pos_data_fields(self, config_id):
        return super()._load_pos_data_fields(config_id) + ['markup_generated', 'markup_payment_reference']
