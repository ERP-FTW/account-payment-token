{
    'name': 'POS CardPointe Receipts',
    'version': '18.0.1.0.0',
    'summary': 'Backend CardPointe terminal receipt reprint for POS payments',
    'category': 'Sales/Point of Sale',
    'license': 'LGPL-3',
    'depends': ['pos_cardpointe_poc'],
    'data': [
        'views/pos_payment_views.xml',
    ],
    'installable': True,
    'application': False,
}
