{
    'name': 'Account Payment Markup',
    'version': '19.0.3.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Add the selected payment method’s fee product at checkout',
    'depends': ['account_payment'],
    'data': ['views/online_method.xml', 'views/payment_portal_templates.xml'],
    'assets': {
        'web.assets_frontend': ['account_payment_markup/static/src/checkout.css'],
    },
    'license': 'LGPL-3',
}
