# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request
from odoo import Command

import json


class TermsController(http.Controller):

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
            sales = SaleOrder.with_context(eadu_message=True).create({
                'partner_id': partner.id,
                'origin': ref,
                'eadu_ident': eadu_ident,
                'order_line': [Command.create(line) for line in lines],
            })
        user = request.env['res.users'].sudo().browse(user_id)
        sales.with_context(eadu_message=True)._message_log(author_id=user.partner_id.id, body=_('Sale Order created/modified from Eadu'))
        return {'sale_order_id': sales.id}

    @http.route('/eadu/1/purchaseordercreate', type='json', auth='public')
    def purchaseorder_adapt(self, eadu_ident, partner_ident, ref, lines, user_id, **kwargs):
        PurchaseOrder = request.env['purchase.order'].sudo()
        purchases = PurchaseOrder.search([('eadu_ident', '=', eadu_ident)], limit=1)
        for line in lines:
            if not line.get('product_id'):
                product = request.env['product.product'].sudo().search([('name', 'like', line['name'])], limit=1)
                if product:
                    line['product_id'] = product.id
                else:
                    line['product_id'] = request.env.ref('eadu.product_not_found').id
        if purchases:
            # adapt lines
            replace_lines = [Command.clear()] + [Command.create(line) for line in lines]
            purchases.write({'order_line': replace_lines})
        else:
            partner = request.env['res.partner'].sudo().search([('id', '=', partner_ident)], limit=1)
            if not partner:
                raise
            purchases = PurchaseOrder.with_context(eadu_message=True).create({
                'partner_id': partner.id,
                'origin': ref,
                'eadu_ident': eadu_ident,
                'order_line': [Command.create(line) for line in lines],
            })
        user = request.env['res.users'].sudo().browse(user_id)
        purchases.with_context(eadu_message=True)._message_log(author_id=user.partner_id.id, body=_('Purchase Order created/modified from Eadu'))
        return {'purchase_id': purchases.id}
