# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"


    def action_eadu_channel_create(self, name, eadu_ident, partner_ids, channel_type="chat"):
        """
        Create a new mail.channel with the given name and partners.
        partner_ids: list of res.partner IDs to add to the channel (required).
        """
        user = self.env.user
        if not user.partner_id.eadu_url:
            raise
        if not partner_ids or not isinstance(partner_ids, list):
            raise
        
        if epa := self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(user.partner_id, 'discuss.channel', eadu_ident):
            return {'channel_id': epa.res_id} 
        partners = self.env['res.partner'].sudo().browse(partner_ids)
        for partner in partners:
            if partner.user_ids:
                puser = partner.user_ids[0]

        if not puser:
            raise
        vals = {
            'name': name,
            'channel_partner_ids': [(4, x) for x in partner_ids],
            'channel_type': channel_type,
        }
        channel = self.env['discuss.channel'].with_user(puser).sudo().create(vals)
        print(channel, channel.id)
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            user.partner_id, 
            'discuss.channel', 
            channel.id, 
            eadu_ident, 
            partner_master=True, # The creator of the channel is considered the master for this record
        )
        return {'channel_id': channel.id}