# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"


    def _eadu_ensure_remote_channel(self, eadu_partner):
        """Ensure this channel exists on the remote instance for eadu_partner.

        Returns the ``eadu.partner.any`` record for this mapping.  When the remote
        call cannot be sent immediately (connection blocked or partner placeholders
        are unresolved), a placeholder record with ``eadu_ident=0`` is returned and
        the call is queued for retry.
        """
        self.ensure_one()
        EaduAny = self.env['eadu.partner.any'].sudo()
        channel_link = EaduAny._search_for_eadu_partner(eadu_partner, 'discuss.channel', self.id)
        if channel_link:
            return channel_link  # Already mapped (eadu_ident may be 0 if still queued).

        # Build the list of remote partner ids, noting which ones are unresolved.
        result_partners = []
        partner_any_ids = []
        for p in self.channel_partner_ids:
            epa = EaduAny._search_for_eadu_partner(eadu_partner, 'res.partner', p.id)
            if epa:
                result_partners.append(epa.eadu_ident if epa.eadu_ident else None)
                partner_any_ids.append(epa.id)

        params = {
            'name': self.name,
            'eadu_ident': self.id,
            'partner_ids': result_partners,
            'channel_type': self.channel_type,
        }

        ident_placeholders = {}
        if any(pid is None for pid in result_partners):
            ident_placeholders['partner_ids'] = partner_any_ids

        return EaduAny._send_or_queue(
            eadu_partner,
            'discuss.channel',
            'action_eadu_channel_create',
            params,
            local_model='discuss.channel',
            local_res_id=self.id,
            result_key='channel_id',
            partner_master=False,
            ident_placeholders=ident_placeholders,
        )

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
        
        if epa := self.env['eadu.partner.any']._search_for_eadu_partner(user.partner_id, 'discuss.channel', eadu_ident):
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
        channel = self.env['discuss.channel'].with_user(puser).create(vals)
        print(channel, channel.id)
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            user.partner_id, 
            'discuss.channel', 
            channel.id, 
            eadu_ident, 
            partner_master=True, # The creator of the channel is considered the master for this record
        )
        return {'channel_id': channel.id}