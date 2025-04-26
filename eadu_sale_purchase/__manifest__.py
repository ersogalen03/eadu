# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "Eadu Sale Purchase",
    'summary': "This module lets a Sale Order automatically create a Purchase Order in the other DB and vice versa",
    'description': """
 This allows to do the purchase order / sale order synchronization between two databases.  
 
    """,
    'category': 'Eadu',
    'version': '0.1',
    'depends': [
        'sale', 
        'purchase',
        'eadu'
    ],
    'data': [
        'views/purchase_order_views.xml',
        'views/sale_order_views.xml',
        'data/product_data.xml',
    ],
    'demo': [
    ],
    'license': 'LGPL-3',
}
