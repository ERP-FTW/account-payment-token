from odoo import api, fields, models
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

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_list = super()._load_pos_data_fields(config_id)
        # An empty native list means all fields. Do not narrow it to only
        # fee evidence; extend explicit loaders (including CardPointe) instead.
        if not fields_list:
            return fields_list
        return list(dict.fromkeys(fields_list + [
            'markup_base_amount', 'markup_rate_applied', 'markup_amount',
            'markup_total_amount', 'markup_amount_source',
            'markup_payment_reference',
        ]))

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
