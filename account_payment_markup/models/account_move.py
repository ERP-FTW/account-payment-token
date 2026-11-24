from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Immutable copy of the manual Register Payment decision.  These fields
    # live on the delegated journal entry so the evidence survives outside the
    # transient wizard and is visible from both payment and move views.
    markup_provider_id = fields.Many2one('payment.provider', copy=False, readonly=True)
    markup_rate_applied = fields.Float(copy=False, readonly=True, digits=(16, 4))
    markup_tax_included = fields.Boolean(copy=False, readonly=True)
    markup_amount_source = fields.Selection(
        [('manual', 'Manual Register Payment'), ('none', 'Not applied')],
        copy=False, readonly=True,
    )
    markup_base_amount = fields.Monetary(copy=False, readonly=True, currency_field='currency_id')
    markup_net_amount = fields.Monetary(copy=False, readonly=True, currency_field='currency_id')
    markup_tax_amount = fields.Monetary(copy=False, readonly=True, currency_field='currency_id')
    markup_gross_amount = fields.Monetary(copy=False, readonly=True, currency_field='currency_id')
    markup_total_amount = fields.Monetary(copy=False, readonly=True, currency_field='currency_id')
    markup_source_payment_id = fields.Many2one(
        'account.payment', copy=False, readonly=True, ondelete='restrict',
    )
    markup_refund_amount = fields.Monetary(
        copy=False, readonly=True, currency_field='currency_id',
    )