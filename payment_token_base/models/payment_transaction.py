from odoo import fields, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    stored_credential_initiator = fields.Selection(
        selection=[
            ('customer', 'Customer Initiated'),
            ('merchant', 'Merchant Initiated'),
        ],
        string='Stored Credential Initiator',
        index=True,
        copy=False,
    )
    stored_credential_schedule = fields.Selection(
        selection=[
            ('unscheduled', 'Unscheduled'),
            ('scheduled', 'Scheduled'),
        ],
        string='Stored Credential Schedule',
        index=True,
        copy=False,
    )

    def _get_stored_credential_semantics(self):
        self.ensure_one()
        return {
            'initiator': self.stored_credential_initiator,
            'schedule': self.stored_credential_schedule,
            'operation': self.operation,
        }
