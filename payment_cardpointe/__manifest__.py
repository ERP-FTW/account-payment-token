{
    'name': 'CardPointe Online Payments',
    'version': '19.0.1.0.0', 'license': 'LGPL-3',
    'author': 'ERP-FTW',
    'depends': ['payment_cardpointe_base', 'account_payment'],
    'post_init_hook': 'post_init_hook',
    'data': ['views/payment_templates.xml', 'data/payment_provider.xml'],
    'assets': {
        'web.assets_frontend': ['payment_cardpointe/static/src/js/payment_form.js'],
        'web.assets_unit_tests': [
            'payment/static/src/interactions/payment_form.js',
            'payment_cardpointe/static/src/js/payment_form.js',
            'payment_cardpointe/static/tests/program.test.js',
        ],
    },
    'installable': True,
}
