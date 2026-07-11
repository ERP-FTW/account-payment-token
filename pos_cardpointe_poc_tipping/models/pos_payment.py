from odoo import api, fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    cardpointe_tip_amount = fields.Monetary(currency_field='currency_id', store=True)
    cardpointe_base_amount = fields.Monetary(currency_field='currency_id', store=True)

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list += ['cardpointe_tip_amount', 'cardpointe_base_amount']
        return list(dict.fromkeys(fields_list))
