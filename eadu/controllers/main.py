# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request



class TermsController(http.Controller):

    @http.route('/eadu/1/saleordercreate', type='http', auth='public')
    def sale_order_create(self, **kwargs):
        
        return


