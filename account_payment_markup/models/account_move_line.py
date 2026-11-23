from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    markup_source_payment_id = fields.Many2one(
        'account.payment', copy=False, readonly=True, ondelete='restrict',
    )
    markup_decision_source = fields.Selection(
        [('manual', 'Manual Register Payment'), ('refund', 'Manual Markup Refund')],
        copy=False, readonly=True,
    )