from odoo import fields, models


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    cardpointe_tip_amount = fields.Monetary(currency_field='currency_id', store=True)
    cardpointe_base_amount = fields.Monetary(currency_field='currency_id', store=True)
