from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CardPointeTerminalConfig(models.Model):
    _inherit = 'pos.cardpointe.terminal.config'

    enable_tips = fields.Boolean(string='Enable Tips', default=False)
    tip_percent_1 = fields.Integer(string='Tip % #1', default=15)
    tip_percent_2 = fields.Integer(string='Tip % #2', default=18)
    tip_percent_3 = fields.Integer(string='Tip % #3', default=20)
    tip_allow_custom = fields.Boolean(string='Allow Custom Tip', default=True)

    @api.constrains('tip_percent_1', 'tip_percent_2', 'tip_percent_3')
    def _check_tip_percents(self):
        for rec in self:
            for value in (rec.tip_percent_1, rec.tip_percent_2, rec.tip_percent_3):
                if value < 0:
                    raise ValidationError(_('Tip percentages must be zero or greater.'))
