# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request
from odoo import Command

import json


class TermsController(http.Controller):


    @http.route('/eadu/1/messagereceive', type='json', auth='public')
    def message_receive(self, partner_ident, user_id, model, res_id, body):
        partner = request.env['res.partner'].sudo().browse(partner_ident)
        # Need some logic to check the original message did not come from Eadu
        if not partner.eadu_ident:
            raise
        user = request.env['res.users'].sudo().browse(user_id)
        import pdb; pdb.set_trace()

        vals = {
            'body': body,
            'author_id': user.partner_id.id,
        }
        vals.update({
            'res_id': partner.id,
            'model': 'res.partner',
        })
        message = request.env['mail.message'].sudo().create(vals)

        return {'message_id': message.id}


    @http.route('/eadu/1/usercreate', type='json', auth='public')
    def user_create(self, partner_ident, eadu_ident, name):
        partner = request.env['res.partner'].sudo().browse(partner_ident)
        if not partner.eadu_ident:
            raise
        vals = {
            'name': name + ' ' + partner.name,
            'login': name + '_' + partner.name,
            'groups_id': [Command.link(request.env.ref('base.group_portal').id)],
            'eadu_ident': eadu_ident,
        }
        
        user = request.env['res.users'].sudo().create(vals)
        return {'user_id': user.id}

    @http.route('/eadu/1/saleordercreate', type='json', auth='public')
    def saleorder_adapt(self, eadu_ident, partner_ident, ref, lines, user_id, **kwargs):
        SaleOrder = request.env['sale.order'].sudo()
        sales = SaleOrder.search([('eadu_ident', '=', eadu_ident)], limit=1)
        for line in lines:
            if not line.get('product_id'):
                product = request.env['product.product'].sudo().search([('name', 'like', line['name'])], limit=1)
                if product:
                    line['product_id'] = product.id
                else:
                    line['product_id'] = request.env.ref('eadu.product_not_found').id
        if sales:
            # adapt lines
            replace_lines = [Command.clear()] + [Command.create(line) for line in lines]
            sales.write({'order_line': replace_lines})
        else:
            partner = request.env['res.partner'].sudo().search([('id', '=', partner_ident)], limit=1)
            if not partner:
                raise
            sales = SaleOrder.create({
                'partner_id': partner.id,
                'origin': ref,
                'eadu_ident': eadu_ident,
                'order_line': [Command.create(line) for line in lines],
            })
        user = request.env['res.users'].sudo().browse(user_id)
        sales._message_log(author_id=user.partner_id.id, body=_('Sale Order created/modified from Eadu'))
        return {'sale_order_id': sales.id}
