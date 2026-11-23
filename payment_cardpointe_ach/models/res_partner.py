from odoo import fields, models

class ResPartner(models.Model):
    _inherit = 'res.partner'

    cp_ach_ccd_agreement_ref = fields.Char(string='Signed one-time ACH company agreement reference', groups='base.group_system', copy=False)
