/** @odoo-module **/
import { PaymentForm } from '@payment/interactions/payment_form';
import { patch } from '@web/core/utils/patch';
import { _t } from '@web/core/l10n/translation';
import '@payment_cardpointe/js/payment_form';

patch(PaymentForm.prototype, {
    _cpFormError(form, retry) {
        const bank = form.querySelector('.o_cp_ach');
        if (bank && !retry && (!bank.querySelector('.o_cp_consent').checked ||
                              !bank.querySelector('.o_cp_account_class:checked'))) {
            return _t('Select a bank account type and authorize the displayed amount.');
        }
        return super._cpFormError(form, retry);
    },
    _cpFormValues(form) {
        const values = super._cpFormValues(form);
        const bank = form.querySelector('.o_cp_ach');
        if (bank) {
            values.consent = bank.querySelector('.o_cp_consent').checked;
            values.account_class = bank.querySelector('.o_cp_account_class:checked')?.value;
            values.consent_amount = bank.dataset.consentAmount;
        }
        return values;
    },
});
