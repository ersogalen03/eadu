# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "Eadu Product",
    'summary': "Bridge between Eadu and the standard Odoo product module",
    'description': """
Products imported from another Eadu database are synchronized directly as native
product.product records. Exact barcode matches are linked automatically; uncertain
imports stay inactive and marked for review. The publisher's sale price becomes a
product.supplierinfo vendor price for the receiver.
    """,
    'category': 'Eadu',
    'version': '0.1',
    'depends': [
        'eadu',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/eadu_product_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Registers /product: into the eadu mention registry
            'eadu_product/static/src/mention/eadu_product_mention.js',
            # XML templates for the product suggestion row
            'eadu_product/static/src/mention/eadu_product_mention.xml',
        ],
    },
    'license': 'LGPL-3',
}
