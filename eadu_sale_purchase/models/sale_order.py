# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    eadu_ident = fields.Integer('Eadu Identification', copy=False)


    def _convert_order_line(self): 
        """ Lines converted to purchase order lines """
        lines = []
        for line in self.order_line:
            lines.append({
                'name': line.product_id.name,
                'product_qty': line.product_uom_qty,
                'price_unit': line.price_unit,
            })
        return lines

    # Button methods

    def button_eadu_sync(self):
        self.ensure_one()
        if not self.partner_id.eadu_ident:
            raise
        # Search user_partner link
        partner_user = self.env['purchase.order']._search_create_partner_user(self.partner_id, self.env.user)

        params = {
            'lines': self._convert_order_line(), 
            'ref': self.name, 
            'eadu_ident': self.id, 
            'user_id': partner_user.eadu_ident
            }
        result = self.partner_id._eadu_call('eadu/1/purchaseordercreate', params)
        self.eadu_ident = result['purchase_id']

