# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class EaduPartnerAny(models.Model):
    _name = "eadu.partner.any"
    _description = "Link between Eadu Partners and other objects in order to know which object has which ID in which db.  Users in this db are however partners in the other db (a partner_id away)"

    partner_id = fields.Many2one('res.partner', 'Eadu Contact', index=True, help="The Eadu contact that represents the other DB") 
    res_model = fields.Char('Resource Model', index=True)
    res_id = fields.Integer('Resource ID', index=True)
    eadu_ident = fields.Integer('Eadu Identification')
    to_sync = fields.Boolean('To Sync')
    master_status = fields.Selection([('partner', 'Partner Is Master'), 
                                      ('me', 'I Am Master')], string='Master Status')

    def _get_record(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def _search_for_eadu_partner(self, eadu_partner, res_model, res_id):
        return self.search([
            ('partner_id', '=', eadu_partner.id),
            ('res_model', '=', res_model),
            ('res_id', '=', res_id),
        ])

    def _search_create_for_eadu_partner(self, eadu_partner, res_model, res_id, eadu_ident, partner_master=False):
        record = self._search_for_eadu_partner(eadu_partner, res_model, res_id)
        if not record:
            record = self.create({
                'partner_id': eadu_partner.id,
                'res_model': res_model,
                'res_id': res_id,
                'eadu_ident': eadu_ident,
                'master_status': 'partner' if partner_master else 'me',
            })
        else:
            record.master_status = 'partner' if partner_master else 'me'
            record.eadu_ident = eadu_ident
        return record
    
    def _model_fields_mapping(self):
        return {
            'res.partner': ['name', 'email', 'phone', 'function', 'street', 'street2', 'zip', 'city', 'state_id', 'country_id', 'company_id'],
            # Include attachment_ids so attachment writes mark record for sync
            'mail.message': ['body', 'attachment_ids'],
            'ir.attachment': ['name', 'mimetype', 'datas', 'res_model', 'res_id'],
            'mail.message.reaction': ['content'],
        }
                
    def _sync_with_others(self, res_model, res_id, vals):
        eadu_anys = self.search([('res_model', '=', res_model), ('res_id', '=', res_id)]) 
        if eadu_anys and vals.keys() & set(self._model_fields_mapping().get(res_model, [])):
            eadu_anys.to_sync = True    
        # Trigger cron
        self.env.ref('eadu.ir_cron_process_eadu_synchronize').sudo()._trigger()
         

    def _synchronize(self):
        """ Will be called by the cron"""
        to_sync = self.search([('to_sync', '=', True)])
        for eadu_any in to_sync:
            record = eadu_any._get_record()
            if not record:
                continue
            vals = {}
            for field in self._model_fields_mapping().get(eadu_any.res_model, []):
                vals[field] = record[field]
                if self.env[eadu_any.res_model]._fields[field].type == 'many2one':
                    vals[field] = vals[field].id if vals[field] else False
                if field == "datas": # could be instanceof bytes?
                    vals[field] = vals[field].decode()
            
            eadu_any.partner_id.sudo()._eadu_call(
                'eadu.partner.any',
                'remote_sync',
                {
                    'res_model': eadu_any.res_model,
                    'res_id': eadu_any.eadu_ident,
                    'vals': vals,
                }
            )
            eadu_any.to_sync = False
        
    def remote_sync(self, res_model, res_id, vals):
        """ Called from remote to update the record """
        eadu_contact = self.env.user.partner_id
        eadu_any = self.sudo()._search_for_eadu_partner(eadu_contact, res_model, res_id)
        if eadu_any:
            record = eadu_any._get_record()
            if record:
                record.sudo().with_context(eadu_message=True).write(vals) # need to think security here