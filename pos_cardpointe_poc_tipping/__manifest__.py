{
    'name': 'POS CardPointe POC Tipping',
    'version': '18.0.1.0.0',
    'summary': 'Tip-at-sale extension for POS CardPointe POC',
    'category': 'Sales/Point of Sale',
    'depends': ['pos_cardpointe_poc'],
    'data': [
        'views/cardpointe_terminal_config_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cardpointe_poc_tipping/static/src/js/pos_cardpointe_poc_tipping.js',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
}
