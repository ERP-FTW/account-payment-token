{
    'name': 'POS Payment Markup',
    'version': '16.0.1.0.0',
    'category': 'Sales/Point of Sale',
    'summary': 'POS card payment markup using account payment markup rules',
    'depends': ['point_of_sale', 'account_payment_markup'],
    'data': ['views/pos_payment_method_views.xml'],
    'assets': {
        'point_of_sale.assets': ['pos_payment_markup/static/src/js/payment_markup.js'],
    },
    'license': 'LGPL-3',
    'installable': True,
}
