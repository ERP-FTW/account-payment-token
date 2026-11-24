from odoo import models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    def _cp_manual_markup_conflict(self):
        # account_payment_markup gates program cards using cp_surcharge_program.
        # A configured manual ACH fee is not a card-program conflict. The
        # adapter still rejects other fee engines through its fees_active guard.
        self.ensure_one()
        return False

    def _cp_checkout_amount(self, amount, currency, partner, method_code):
        self.ensure_one()
        return self._markup_portal_disclosure(
            amount, currency, partner, payment_method_code=method_code)['total_amount']
