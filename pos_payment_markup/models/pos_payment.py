from odoo import fields, models
from odoo.exceptions import UserError


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    markup_base_amount = fields.Monetary(currency_field='currency_id', copy=False)
    markup_rate_applied = fields.Float(copy=False, digits=(16, 4))
    markup_amount = fields.Monetary(currency_field='currency_id', copy=False)
    markup_total_amount = fields.Monetary(currency_field='currency_id', copy=False)
    markup_amount_source = fields.Selection([
        ('local', 'Local calculation'), ('processor', 'Processor authoritative amount'),
        ('none', 'Not applied')
    ], copy=False)
    markup_payment_reference = fields.Char(copy=False, index=True)

    def write(self, values):
        protected = {'amount', 'payment_method_id', 'pos_order_id', 'uuid'} | {
            field for field in self._fields if field.startswith('markup_')
        }
        if protected.intersection(values) and self.filtered(
                lambda payment: payment.pos_order_id.state in ['paid', 'done', 'invoiced']
                and payment.markup_amount):
            raise UserError('Finalized payment fee evidence is immutable.')
        return super().write(values)

    def _markup_apply_from_ui(self, values):
        self.ensure_one()
        self.write({key: values.get(key) for key in (
            'markup_base_amount', 'markup_rate_applied', 'markup_amount',
            'markup_total_amount', 'markup_amount_source',
            'markup_payment_reference') if key in values})
