{
    'name': 'POS CardPointe Receipts',
    'version': '18.0.1.0.0',
    'summary': 'Backend CardPointe terminal receipt reprint scaffold for POS payments',
    'category': 'Point of Sale',
    'license': 'LGPL-3',
    'depends': ['pos_cardpointe_poc'],
    'data': [
        'security/ir.model.access.csv',
        'views/pos_payment_views.xml',
    ],
    'installable': True,
    'application': False,
}
