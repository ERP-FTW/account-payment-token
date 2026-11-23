{
    'name': 'CardPointe Online Payments',
    'version': '18.0.1.0.0', 'license': 'LGPL-3',
    'author': 'ERP-FTW',
    'depends': ['payment_cardpointe_base', 'account_payment'],
    'post_init_hook': 'post_init_hook',
    'data': ['views/payment_templates.xml', 'data/payment_provider.xml'],
    'assets': {'web.assets_frontend': ['payment_cardpointe/static/src/js/payment_form.js']},
    'installable': True,
}
