# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request
from odoo import Command

import json


class TermsController(http.Controller):

    @http.route('/eadu/1/usercreate', type='json', auth='public')
    def user_create(self, partner_id, eadu_id,  name):
        partner = request.env['res.partner'].sudo().browse(partner_id)
        if not partner.eadu_ident:
            return
        vals = {
            'name': name + ' ' + partner.name,
            'login': name + '_' + partner.name,
            'groups': [Command.link(self.env.ref('base.group_portal'))]
        } 
        
        user = request.env.sudo().create(vals)
        request.env['eadu.user.partner'].sudo().create({
            'user_id': user.id,
            'partner_id': partner_id,
            'eadu_ident': eadu_id,
        })

    @http.route('/eadu/1/saleordercreate', type='json', auth='public')
    def saleorder_adapt(self, partner_ident, ref, lines, **kwargs):
        SaleOrder = request.env['sale.order'].sudo()
        sales = SaleOrder.search([('partner_id.eadu_ident', '=', partner_ident), ('origin', '=', ref)], limit=1)
        if sales:
            # adapt lines
            sales.write({'line_ids': lines})
        else:
            partner = request.env['res.partner'].sudo().search([('eadu_ident', '=', partner_ident)], limit=1)
            if not partner:
                raise
            SaleOrder.create({
                'partner_id': partner.id,
                'origin': ref,
                'order_line': lines,
            })

