from odoo import api, fields, models


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    cardpointe_config_id = fields.Many2one('pos.cardpointe.terminal.config', string='CardPointe Config')
    cardpointe_manual_entry_enabled = fields.Boolean(
        string='Enable Manual Card Entry',
        default=False,
        help='Allow POS cashiers to use CardPointe Hosted iFrame Tokenizer as a manual card entry fallback.',
    )

    def _load_pos_data_fields(self, config_id):
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list += ['cardpointe_config_id', 'cardpointe_manual_entry_enabled']
        return list(dict.fromkeys(fields_list))

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('cardpointe_poc', 'CardPointe POC')]

    @api.onchange('use_payment_terminal')
    def _onchange_use_payment_terminal(self):
        super()._onchange_use_payment_terminal()
        if self.use_payment_terminal != 'cardpointe_poc':
            self.cardpointe_config_id = False
            self.cardpointe_manual_entry_enabled = False
