from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    cardpointe_tip_enabled = fields.Boolean(
        related='cardpointe_config_id.enable_tips',
        readonly=True,
    )

    def _load_pos_data_fields(self, config_id):
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list.append('cardpointe_tip_enabled')
        return list(dict.fromkeys(fields_list))
