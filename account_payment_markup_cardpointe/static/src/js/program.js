/** @odoo-module **/
import { PaymentForm } from '@payment/interactions/payment_form';
import { patch } from '@web/core/utils/patch';
import { _t } from '@web/core/l10n/translation';
import '@payment_cardpointe/js/payment_form';

patch(PaymentForm.prototype, {
    _cpFormError(form, retry) {
        const program = form.querySelector('.o_cp_program');
        if (program?.dataset.quoteId && !program.querySelector('.o_cp_quote_confirm').checked) {
            return _t('Accept the displayed total before authorizing.');
        }
        return super._cpFormError(form, retry);
    },
    _cpFormValues(form) {
        const values = super._cpFormValues(form);
        const program = form.querySelector('.o_cp_program');
        if (program) {
            values.postal = program.querySelector('.o_cp_postal').value;
            values.quote_id = program.dataset.quoteId || null;
        }
        return values;
    },
    _cpHandleResult(form, result) {
        const program = form.querySelector('.o_cp_program');
        if (!program || !['quote', 'quote_error'].includes(result.state)) {
            return super._cpHandleResult(form, result);
        }
        program.dataset.quoteId = result.quote_id || '';
        program.querySelector('.o_cp_quote_confirm').checked = false;
        program.querySelector('.o_cp_quote_text').textContent = result.quote
            ? _t('Subtotal %s, gateway fee %s, total %s %s.', result.quote.subtotal, result.quote.fee,
                 result.quote.total, result.quote.currency)
            : result.message;
        return true;
    },
});
