# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"


    def _eadu_channel_partners(self):
        eadu_partners = self.env['res.partner']
        for partner in self.channel_partner_ids:
            if eadu_partner := partner.eadu_connection_partner_id:
                eadu_partners |= eadu_partner
        return eadu_partners

    def _eadu_remote_member_partners(self):
        self.ensure_one()
        partners = self.channel_partner_ids
        if self.parent_channel_id:
            partners |= self.parent_channel_id.channel_partner_ids
        return partners

    def _eadu_update_field_names(self):
        return ['name', 'description', 'image_128']

    def _eadu_prepare_update_fields(self, field_names=None):
        allowed_fields = self._eadu_update_field_names()
        if field_names is not None:
            field_names = [field for field in allowed_fields if field in field_names]
        else:
            field_names = allowed_fields
        return self.env['eadu.partner.any'].sudo()._serialize_fields(self, field_names)

    def _eadu_send_update(self, eadu_any, field_names=None):
        fields_values = self._eadu_prepare_update_fields(field_names)
        if not fields_values:
            return
        self.env['eadu.partner.any'].sudo()._send_or_queue(
            eadu_any.partner_id,
            'discuss.channel',
            'action_eadu_channel_update',
            {
                'eadu_ident': eadu_any.res_id,
                'fields': fields_values,
            },
            eadu_any_ref=eadu_any,
        )

    def _eadu_sync_sub_channel_create(self):
        EaduAny = self.env['eadu.partner.any'].sudo()
        for channel in self:
            if not channel.parent_channel_id:
                continue

            parent_links = EaduAny.search([
                ('res_model', '=', 'discuss.channel'),
                ('res_id', '=', channel.parent_channel_id.id),
            ])
            eadu_partners = parent_links.mapped('partner_id') or channel._eadu_channel_partners()
            if not eadu_partners:
                continue

            for eadu_partner in eadu_partners:
                for partner in channel._eadu_remote_member_partners():
                    if partner.eadu_connection_partner_id == eadu_partner:
                        continue
                    partner._eadu_ensure_remote_partner(eadu_partner)
                channel._eadu_ensure_remote_channel(eadu_partner)

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
        for p in self._eadu_remote_member_partners():
            epa = EaduAny._search_for_eadu_partner(eadu_partner, 'res.partner', p.id)
            if epa:
                result_partners.append(epa.eadu_ident if epa.eadu_ident else None)
                partner_any_ids.append(epa.id)

        params = {
            'name': self.name,
            'eadu_ident': self.id,
            'partner_ids': result_partners,
            'channel_type': self.channel_type,
            'description': self.description,
            'image_128': self._eadu_prepare_update_fields(['image_128']).get('image_128'),
        }

        ident_placeholders = {}
        if any(pid is None for pid in result_partners):
            ident_placeholders['partner_ids'] = partner_any_ids
        if self.parent_channel_id:
            parent_link = self.parent_channel_id._eadu_ensure_remote_channel(eadu_partner)
            if not parent_link:
                return False
            params['parent_channel_id'] = parent_link.eadu_ident or None
            if not parent_link.eadu_ident:
                ident_placeholders['parent_channel_id'] = parent_link.id
        if self.from_message_id:
            message_link = EaduAny._search_for_eadu_partner(
                eadu_partner, 'mail.message', self.from_message_id.id
            )
            if message_link:
                params['from_message_id'] = message_link.eadu_ident or None
                if not message_link.eadu_ident:
                    ident_placeholders['from_message_id'] = message_link.id

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

    def _create_sub_channel(self, from_message_id=None, name=None):
        sub_channel = super()._create_sub_channel(from_message_id=from_message_id, name=name)
        sub_channel._eadu_sync_sub_channel_create()
        return sub_channel

    def set_message_pin(self, message_id, pinned):
        result = super().set_message_pin(message_id=message_id, pinned=pinned)
        message = self.env['mail.message'].sudo().browse(message_id).exists()
        if message and message.model == 'discuss.channel' and message.res_id in self.ids:
            EaduAny = self.env['eadu.partner.any'].sudo()
            for eadu_any in EaduAny.search([
                ('res_model', '=', 'mail.message'),
                ('res_id', '=', message.id),
            ]):
                message._eadu_send_update(eadu_any, {'pinned_at'})
        return result

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            if changed_fields:
                EaduAny = self.env['eadu.partner.any'].sudo()
                for channel in self:
                    for eadu_any in EaduAny.search([
                        ('res_model', '=', 'discuss.channel'),
                        ('res_id', '=', channel.id),
                    ]):
                        channel._eadu_send_update(eadu_any, changed_fields)
        return res

    def action_eadu_channel_create(
        self, name, eadu_ident, partner_ids, channel_type="chat",
        parent_channel_id=None, from_message_id=None, description=None,
        image_128=None,
    ):
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
        puser = user
        for partner in partners:
            if partner.user_ids:
                puser = partner.user_ids[0]

        if not puser:
            raise
        vals = {
            'name': name,
            'channel_partner_ids': [(4, x) for x in partner_ids],
            'channel_type': channel_type,
            'description': description,
            'image_128': image_128,
        }
        if parent_channel_id:
            vals['parent_channel_id'] = parent_channel_id
        if from_message_id:
            vals['from_message_id'] = from_message_id
        channel = self.env['discuss.channel'].with_user(puser).sudo().create(vals)
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            user.partner_id,
            'discuss.channel',
            channel.id,
            eadu_ident,
            partner_master=True, # The creator of the channel is considered the master for this record
        )
        return {'channel_id': channel.id}

    def action_eadu_channel_update(self, eadu_ident, fields):
        user = self.env.user
        if not user.partner_id.eadu_url:
            raise
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            user.partner_id, 'discuss.channel', eadu_ident
        )
        if not eadu_any:
            return {'result': False}
        vals = {
            field_name: value
            for field_name, value in (fields or {}).items()
            if field_name in self._eadu_update_field_names()
        }
        if vals:
            eadu_any._get_record().sudo().with_context(eadu_message=True).write(vals)
        return {'result': True}
