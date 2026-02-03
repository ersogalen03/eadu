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
            # find all remote mappings for this message and sync reaction creation
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'mail.message'), ('res_id', '=', message.id)
            ])
            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id
                # map current reacting partner to remote partner
                remote_partner = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                    eadu_contact, 'res.partner', self.env.user.partner_id.id
                )
                if not remote_partner:
                    continue
                params = {
                    'message_id': eadu_any.eadu_ident,
                    'content': reaction.content,
                    'partner_id': remote_partner.eadu_ident,
                    'eadu_ident': reaction.id,
                }
                res = eadu_contact.sudo()._eadu_call('mail.message.reaction', 'action_eadu_receive', params)
                if res and 'reaction_id' in res:
                    self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                        eadu_contact, 'mail.message.reaction', reaction.id, res['reaction_id']
                    )

            reactions |= reaction
        return reactions

    def unlink(self):
        # propagate removal remotely when applicable
        for reaction in self:
            eadu_anys = self.env['eadu.partner.any'].sudo().search([
                ('res_model', '=', 'mail.message.reaction'), ('res_id', '=', reaction.id)
            ])
            for eadu_any in eadu_anys:
                eadu_contact = eadu_any.partner_id
                try:
                    eadu_contact.sudo()._eadu_call('mail.message.reaction', 'action_eadu_remove', {
                        'eadu_ident': eadu_any.eadu_ident,
                    })
                except Exception:
                    # best-effort removal; continue
                    pass
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
            eadu_contact, 'mail.message.reaction', reaction.id, eadu_ident
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
