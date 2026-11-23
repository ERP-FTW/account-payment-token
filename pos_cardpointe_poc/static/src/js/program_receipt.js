/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { PosPayment } from '@point_of_sale/app/models/pos_payment';

patch(PosPayment.prototype, {
    export_for_printing() {
        return {...super.export_for_printing(...arguments),
            cardpointe_program_receipt: this.cardpointe_program_receipt || false};
    },
});
