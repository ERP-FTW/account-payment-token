# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _cardpointe_tx_log_context(self):
        self.ensure_one()
        return {
            'reference': self.reference,
            'amount': self.amount,
            'currency': self.currency_id.name,
            'provider_id': self.provider_id.id,
            'partner_id': self.partner_id.id,
        }

    def _cardpointe_set_provider_reference(self, data):
        self.ensure_one()
        provider_reference = data.get('retref') or data.get('authcode') or data.get('reference')
        if provider_reference:
            self.provider_reference = provider_reference
        if 'acquirer_reference' in self._fields:
            acquirer_reference = data.get('retref') or data.get('authcode')
            if acquirer_reference:
                self.acquirer_reference = acquirer_reference

    def _cardpointe_fail(self, message, code=None):
        self.ensure_one()
        final_message = f"{message} ({code})" if code else message
        self._set_error(final_message)

    def _cardpointe_done(self, message=None):
        self.ensure_one()
        if message:
            self.state_message = message
        self._set_done()
