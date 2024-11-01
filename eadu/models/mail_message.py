# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

import re

class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, vals):
        if self.env.context.get('eadu_message'):
            return super(MailMessage, self).create(vals)
        for val in vals:
            result = False
            if val.get('body'):
                body = val['body']
                model = val.get('model')
                res_id = val.get('res_id')
                if model == 'discuss.channel':
                    channel = self.env['discuss.channel'].browse(res_id)
                    partners = channel.channel_partner_ids
                    user = self.env.user.partner_id
                    ext_user = partners.filtered(lambda p: p != user)
                    if ext_user:
                        ext_user = ext_user[0]

                    if ext_user and (partner := ext_user.commercial_partner_id) and partner.eadu_ident:
                        partner_user = self.env['purchase.order']._search_create_partner_user(partner.commercial_partner_id, self.env.user)
                        if not channel.eadu_ident:
                            result = self.env['purchase.order']._eadu_call(
                                partner, 
                                'eadu/1/channelcreate', 
                                {
                                    'name': channel.name,
                                    'eadu_ident': channel.id,
                                    'from_user_id': partner_user.eadu_ident,
                                    'to_user_id': ext_user.user_ids.eadu_ident,
                                }
                            )
                            channel.eadu_ident = result['channel_id']
                        res_id = channel.eadu_ident
                        partner_parent = partner
                        result = True

                elif match:= re.search('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', body):
                    partner = self.env['res.partner'].browse(int(match.group(1)))
                    user = partner.user_ids.filtered(lambda u: u.eadu_ident)
                    if partner.commercial_partner_id and partner.commercial_partner_id.eadu_ident and user:

                        body = re.sub('data-oe-id=\"([0-9]+)\" data-oe-model=\"res.partner\"', str(user.eadu_ident), body)
                        if model == 'sale.order':
                            ident = self.env['sale.order'].browse(res_id).eadu_ident
                            if ident:
                                model = 'purchase.order'
                                res_id = ident
                                result = True
                        elif model == 'purchase.order':
                            ident = self.env['purchase.order'].browse(res_id).eadu_ident
                            if ident:
                                model = 'sale.order'
                                res_id = ident
                                result = True
                        

                        partner_user = self.env['purchase.order']._search_create_partner_user(partner.commercial_partner_id, self.env.user)
                        partner_parent = partner.commercial_partner_id

                if result:
                    result = self.env['purchase.order']._eadu_call(
                        partner_parent, 
                        'eadu/1/messagereceive', 
                        {
                        'model': model, 
                        'res_id': res_id,
                        'body': body,
                        'user_id': partner_user.eadu_ident,
                        }
                    )
        return super(MailMessage, self).create(val)