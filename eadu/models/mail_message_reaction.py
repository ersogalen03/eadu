# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class MailMessageReaction(models.Model):
    _inherit = 'mail.message.reaction'

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

    # Remote entry points
    def action_eadu_receive(self, message_id, content, partner_id, eadu_ident):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        # map remote message to local message
        m_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
            eadu_contact, 'mail.message', message_id
        )
        p_map = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
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
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
            eadu_contact, 'mail.message.reaction', eadu_ident
        )
        if eadu_any:
            self.env['mail.message.reaction'].sudo().browse(eadu_any.res_id).unlink()
            eadu_any.unlink()
