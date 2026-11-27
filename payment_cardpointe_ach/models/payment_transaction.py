from decimal import Decimal, InvalidOperation
from odoo import fields, models
from odoo.exceptions import ValidationError
from .payment_provider import ACCOUNT_CLASSES, CONSENT_VERSION


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    cp_ach_consent_text = fields.Text(readonly=True, copy=False)
    cp_ach_consent_version = fields.Char(readonly=True, copy=False)
    cp_ach_consent_at = fields.Datetime(readonly=True, copy=False)
    cp_ach_account_class = fields.Selection([(k,v[0]) for k,v in ACCOUNT_CLASSES.items()], readonly=True, copy=False)
    cp_ach_entry_code = fields.Char(readonly=True, copy=False)

    def _cp_prepare_authorization(self, token, options):
        if self.payment_method_id.code != 'cardpointe_ach':
            return super()._cp_prepare_authorization(token, options)
        account_class = options.get('account_class')
        if self.currency_id.name != 'USD' or options.get('consent') is not True:
            raise ValidationError('USD and explicit one-time authorization are required for ACH.')
        try:
            matches = Decimal(str(options.get('consent_amount'))) == Decimal('%.2f' % self.amount)
        except (InvalidOperation, ValueError, TypeError):
            matches = False
        if not matches:
            raise ValidationError('The payment amount changed. Review and authorize the current total.')
        if account_class not in dict(self.provider_id._cp_ach_options()):
            raise ValidationError('This bank account and entry-code combination is not enabled for this merchant.')
        _, accttype, entry = ACCOUNT_CLASSES[account_class]
        company = self.partner_id.commercial_partner_id
        if entry == 'CCD' and (not company.is_company or not company.cp_ach_ccd_agreement_ref):
            raise ValidationError('A signed ACH company agreement for this business payer is required.')
        self.write({'cp_ach_consent_text': self.provider_id._cp_ach_consent(self.amount,self.currency_id), 'cp_ach_consent_version': CONSENT_VERSION, 'cp_ach_consent_at': fields.Datetime.now(), 'cp_ach_account_class': account_class, 'cp_ach_entry_code': entry})
        return None, {'accttype':accttype, 'achEntryCode':entry, 'name':self.partner_name[:30], **({'achDescription':'PURCHASE'} if entry=='WEB' else {})}
