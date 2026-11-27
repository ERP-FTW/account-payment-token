import base64

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    method = env.ref('payment_cardpointe_ach.payment_method_ach', raise_if_not_found=False)
    if method and not method.image:
        with file_open('payment/static/img/ach_direct_debit.png', 'rb') as icon:
            method.image = base64.b64encode(icon.read())
