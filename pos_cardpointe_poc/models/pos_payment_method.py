from odoo import api, fields, models


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    cardpointe_config_id = fields.Many2one('pos.cardpointe.terminal.config', string='CardPointe Config')

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('cardpointe_poc', 'CardPointe POC')]

    @api.onchange('use_payment_terminal')
    def _onchange_use_payment_terminal(self):
        super()._onchange_use_payment_terminal()
        if self.use_payment_terminal != 'cardpointe_poc':
            self.cardpointe_config_id = False
