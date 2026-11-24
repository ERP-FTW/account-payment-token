from odoo import fields, models


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

    def _markup_apply_from_ui(self, values):
        self.ensure_one()
        self.write({key: values.get(key) for key in (
            'markup_base_amount', 'markup_rate_applied', 'markup_amount',
            'markup_total_amount', 'markup_amount_source',
            'markup_payment_reference') if key in values})
