from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CardPointeTerminalConfig(models.Model):
    _inherit = 'pos.cardpointe.terminal.config'

    enable_tips = fields.Boolean(string='Enable Tips', default=False)
    tip_prompt = fields.Char(string='Tip Prompt', default='Select tip amount')
    tip_percent_1 = fields.Integer(string='Tip % #1', default=15)
    tip_percent_2 = fields.Integer(string='Tip % #2', default=18)
    tip_percent_3 = fields.Integer(string='Tip % #3', default=20)
    tip_percent_4 = fields.Integer(string='Tip % #4', default=25)
    tip_presets_count = fields.Selection(
        [('2', '2 Presets'), ('3', '3 Presets'), ('4', '4 Presets')],
        string='Preset Count',
        default='3',
        required=True,
    )
    tip_allow_custom = fields.Boolean(string='Allow Custom Tip', default=True)
    tip_include_amount_display = fields.Boolean(string='Include Amount Display', default=True)

    @api.constrains('tip_percent_1', 'tip_percent_2', 'tip_percent_3', 'tip_percent_4')
    def _check_tip_percents(self):
        for rec in self:
            for value in (rec.tip_percent_1, rec.tip_percent_2, rec.tip_percent_3, rec.tip_percent_4):
                if value < 0:
                    raise ValidationError(_('Tip percentages must be zero or greater.'))

    @api.constrains('tip_presets_count', 'tip_allow_custom')
    def _check_tip_presets_count(self):
        for rec in self:
            if rec.tip_allow_custom and rec.tip_presets_count == '4':
                raise ValidationError(_(
                    'CardPointe allows max 3 tipPercentPresets when includeCustomTipAmount is enabled. '
                    'Use 2 or 3 presets, or disable custom tip amount to use 4 presets.'
                ))
