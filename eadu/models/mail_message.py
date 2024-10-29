# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

import re

class MailMessage(models.Model):
    _inherit = "mail.message"

    
    def create(self, vals):
        for val in vals:
            if val.get('body'):
                body = val['body']
                match = re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body)
                if match:
                    partner = self.env['res.partner'].browse(int(match.group(1)))
                    
                    if partner.parent_id and partner.parent_id.eadu_ident:
                        model = val.get('model')
                        res_id = val.get('res_id')
                        partner_user = self.env['eadu.partner.user'].sudo().search([('user_id', '=', self.env.user.id)], limit=1)
                        if partner_user:
                            result = self.env['purchase.order']._eadu_call(
                                partner.parent_id, 
                                '/eadu/1/messagereceive', 
                                {
                                'model': model, 
                                'res_id': res_id,
                                'body': body,
                                'user_id': partner_user.eadu_ident,
                                }
                            )
                        

        return super(MailMessage, self).create(val)