# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "EADU Demo - Ghorman",
    'summary': "Demo planet database for Ghorman textile trade through EADU",
    'description': """
WARNING: This module is for demo/story databases only. Do not install it in
production: it intentionally renames the main company and administrator and
loads fictional products, contacts, and images.

Transforms a demo database into Ghorman: an embassy company, Ghorman users, and
rich textile products with attributes and variants for EADU product scenarios.
    """,
    'category': 'Eadu/Demo',
    'version': '0.1',
    'depends': [
        'eadu_product',
    ],
    'data': [
        'data/planet_data.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'license': 'LGPL-3',
}
