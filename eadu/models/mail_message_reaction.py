# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class MailMessageReaction(models.Model):
    _inherit = 'mail.message.reaction'

    def _eadu_update_field_names(self):
        return ['content']

    def _eadu_prepare_update_fields(self, field_names=None):
        allowed_fields = self._eadu_update_field_names()
        if field_names is not None:
            field_names = set(field_names)
            allowed_fields = [field for field in allowed_fields if field in field_names]
        return self.env['eadu.partner.any'].sudo()._serialize_fields(self, allowed_fields)

    def _eadu_send_update(self, eadu_any, field_names=None):
        self.ensure_one()
        vals = self._eadu_prepare_update_fields(field_names)
        if vals:
            self.env['eadu.partner.any'].sudo()._send_or_queue(
                eadu_any.partner_id,
                'mail.message.reaction',
                'action_eadu_update',
                {
                    'eadu_ident': eadu_any.res_id,
                    'fields': vals,
                },
                eadu_any_ref=eadu_any,
            )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('eadu_reaction'):
            return super().create(vals_list)

        reactions = self.env['mail.message.reaction']
        for vals in vals_list:
            reaction = super(MailMessageReaction, self).create(vals)

            message = reaction.message_id
            # Find all remote mappings for this message and sync reaction creation.
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'mail.message'), ('res_id', '=', message.id)
            ])
            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id
                remote_partner = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                    eadu_contact, 'res.partner', self.env.user.partner_id.id
                )
                if not remote_partner:
                    continue

                # Build ident_placeholders for any unresolved references.
                ident_placeholders = {}
                if not eadu_any.eadu_ident:
                    ident_placeholders['message_id'] = eadu_any.id
                if not remote_partner.eadu_ident:
                    ident_placeholders['partner_id'] = remote_partner.id

                self.env['eadu.partner.any'].sudo()._send_or_queue(
                    eadu_contact,
                    'mail.message.reaction',
                    'action_eadu_receive',
                    {
                        'message_id': eadu_any.eadu_ident or None,
                        'content': reaction.content,
                        'partner_id': remote_partner.eadu_ident or None,
                        'eadu_ident': reaction.id,
                    },
                    local_model='mail.message.reaction',
                    local_res_id=reaction.id,
                    result_key='reaction_id',
                    partner_master=False,
                    ident_placeholders=ident_placeholders,
                )

            reactions |= reaction
        return reactions

    def unlink(self):
        # Propagate removal remotely when applicable.
        for reaction in self:
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'mail.message.reaction'), ('res_id', '=', reaction.id)
            ])
            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id
                if not eadu_any.eadu_ident and eadu_any.pending_calls:
                    # Creation was queued but never sent; cancel it.
                    eadu_any.unlink()
                    continue
                self.env['eadu.partner.any'].sudo()._send_or_queue(
                    eadu_contact,
                    'mail.message.reaction',
                    'action_eadu_remove',
                    {'eadu_ident': eadu_any.eadu_ident},
                    eadu_any_ref=eadu_any,
                    post_action='remove_eadu_any',
                )
        return super().unlink()

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_reaction'):
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            if not changed_fields:
                return res
            EaduAny = self.env['eadu.partner.any'].sudo()
            for reaction in self:
                eadu_anys = EaduAny.search([
                    ('res_model', '=', 'mail.message.reaction'),
                    ('res_id', '=', reaction.id),
                ])
                for eadu_any in eadu_anys:
                    reaction._eadu_send_update(eadu_any, changed_fields)
        return res

    # Remote entry points
    def action_eadu_receive(self, message_id, content, partner_id, eadu_ident):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        # map remote message to local message
        m_map = self.env['eadu.partner.any']._search_for_eadu_partner(
            eadu_contact, 'mail.message', message_id
        )
        p_map = self.env['eadu.partner.any']._search_for_eadu_partner(
            eadu_contact, 'res.partner', partner_id
        )
        if not m_map or not p_map:
            return {'reaction_id': False}

        reaction = self.env['mail.message.reaction'].sudo().with_context(eadu_reaction=True).create({
            'message_id': m_map.res_id,
            'content': content,
            'partner_id': p_map.res_id,
        })

        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
            eadu_contact, 'mail.message.reaction', reaction.id, eadu_ident,
            partner_master=True,
        )
        return {'reaction_id': reaction.id}

    def action_eadu_remove(self, eadu_ident):
        eadu_contact = self.env.user.partner_id
        eadu_any = self.env['eadu.partner.any']._search_for_eadu_partner(
            eadu_contact, 'mail.message.reaction', eadu_ident
        )
        if eadu_any:
            self.env['mail.message.reaction'].browse(eadu_any.res_id).unlink()
            eadu_any.sudo().unlink()

    def action_eadu_update(self, eadu_ident, fields):
        eadu_contact = self.env.user.partner_id
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            eadu_contact, 'mail.message.reaction', eadu_ident
        )
        if not eadu_any:
            return {'result': False}
        vals = {}
        if fields and 'content' in fields:
            vals['content'] = fields['content']
        if vals:
            eadu_any._get_record().sudo().with_context(eadu_reaction=True).write(vals)
        return {'result': True}
