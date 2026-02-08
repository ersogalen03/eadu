# Part of Eadu. See LICENSE file for full copyright and licensing details.

{
    'name': "Eadu",
    'summary': "This is the base module to allow setting up the connection between two databases",
    'description': """
On the partner fill in his URL and your id in his db. 
    """,
    'category': 'Eadu',
    'version': '0.1',
    'depends': [
        'mail',
    ],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/res_partner_views.xml',
        'security/ir_rule.xml',
    ],
    'demo': [
    ],
    'license': 'LGPL-3',
}
