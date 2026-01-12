# Part of Eadu. See LICENSE file for full copyright and licensing details.

import requests
import base64
import odoo
import threading

from odoo import api, fields, models, Command, _
from odoo.addons.mail.tools.discuss import Store


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Linking 2 companies
    eadu_url = fields.Char("Other Instance URL", copy=False)
    eadu_apikey = fields.Char("Other Instance API Key", copy=False, groups="base.group_system")
    eadu_db = fields.Char("Other Instance DB", copy=False)

    # For individuals of the contact company (still not sure we need this one)
    eadu_ident = fields.Integer("Which partner_id do I have in the other DB", copy=False) # For contacts

    # This should be in a wizard instead
    eadu_exchanged = fields.Char('Token Exchanged', copy=False)


    def _get_eadu_partner(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        eadu_contact = partner.child_ids.filtered(lambda p: p.eadu_url)
        return eadu_contact and eadu_contact[0] or eadu_contact

    def _get_db_name(self):
        db = odoo.tools.config['db_name']
        # If the database name is not provided on the command-line,
        # use the one on the thread (which means if it is provided on
        # the command-line, this will break when installing another
        # database from XML-RPC).
        if not db and hasattr(threading.current_thread(), 'dbname'):
            return threading.current_thread().dbname
        return db and db[0] or None

    def _create_update_eadu_child_partner(self):
        partner = self.commercial_partner_id
        eadu_contact = partner.child_ids.filtered(lambda p: p.eadu_url)
        if not eadu_contact:
            eadu_contact = self.env['res.partner'].search([('name', '=', "EADU" + partner.name), ('parent_id', '=', partner.id)], limit=1)
            if not eadu_contact:
                eadu_contact = self.env['res.partner'].create({
                    'name': "EADU" + partner.name,
                    'parent_id': partner.id,
                    'company_type': 'person',
                    'type': 'other', 
                })
            eadu_user = self.env['res.users'].search([('partner_id', '=', eadu_contact.id), 
                                                      ('is_eadu_user', '=', True)])
            if not eadu_user:        
                eadu_user = self.env['res.users'].create({
                    'partner_id': eadu_contact.id,
                    'login': "EADU" + self.name.strip().strip('#'),
                    'is_eadu_user': True,
                    'group_ids': [Command.link(self.env.ref('base.group_portal').id)],
                })
        return eadu_contact
 
    def _generate_eadu_key(self):
        """ Generate an API key for an EADU contact (with linked user)"""
        # TODO: delete existing keys
        # Generate key
        ApiKeys = self.env['res.users.apikeys']

        user = self.main_user_id
        api_key = ApiKeys.with_user(user).sudo()._generate(scope='rpc', 
                                                           name='Eadu connection',
                                                           expiration_date=None
                                                           )
        return api_key

    def _create_child_contact(self, name, eadu_ident):
        """
            self is the eadu contact
        """
        self.ensure_one()
        partner = self.commercial_partner_id
        child_partner = partner.child_ids.filtered(lambda p: p.name == name)
        eadu_rec = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(self, 'res.partner', child_partner.id)
        if eadu_rec:
            eadu_rec._get_record().name = name
        elif child_partner:
            # could be just a create
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(self, 'res.partner', child_partner.id, eadu_ident)
        else:
            child_partner = self.env['res.partner'].create({
                'name': name,
                'parent_id': partner.id,
                'company_type': 'person',
                'type': 'contact', 
            })
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(self, 'res.partner', child_partner.id, eadu_ident)
        return child_partner


    def button_generate_eadu_exchange(self):
        self.ensure_one()
        # Check if there is a child partner which is linked to an is_eadu user
        eadu_contact = self._create_update_eadu_child_partner()
        api_key = eadu_contact._generate_eadu_key()

        web_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        dbname = self._get_db_name()
        self.env.cr.commit()
        cuser = self.env.user # To already create yourself in the other db (if you have not been already)
        raise odoo.exceptions.UserError(
            _("Copy/paste and tell your contact to use the following code on the partner form of you in his Odoo instance:") + "\n" 
            + base64.b64encode('#'.join([web_url, api_key, dbname, cuser.name, str(cuser.partner_id.id)]).encode()).decode()
        )

    def button_process_eadu_exchanged(self):
        self.ensure_one()
        # Decode the exchange token
        con_str = base64.b64decode(self.eadu_exchanged.encode()).decode()
        con_arr = con_str.split('#')


        eadu_contact = self._create_update_eadu_child_partner()
        eadu_contact.eadu_url = con_arr[0]
        eadu_contact.eadu_apikey = con_arr[1]
        eadu_contact.eadu_db = con_arr[2]
        apikey = eadu_contact._generate_eadu_key()
        connecting_contact = eadu_contact._create_child_contact(con_arr[3], int(con_arr[4]))
        cuser = self.env.user
        res = eadu_contact._eadu_call('res.partner', 'action_connect_eadu', {
            'apikey': apikey,
            'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
            'db': self._get_db_name(),
            'cusername': cuser.name,
            'cpartnereadu': cuser.partner_id.id,
            'ypartnereadu': connecting_contact.id, # newly created partner in this db
            'ypartnerid': con_arr[4], # for the contacted db to verify who started it originally
        })
        ypartnerid = res['result']
        if ypartnerid:
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(self, 'res.users', cuser.id, ypartnerid)

    def action_connect_eadu(self, apikey, url, db, cusername, cpartnereadu, ypartnereadu, ypartnerid):
        user = self.env.user
        eadu_contact = user.partner_id
        if not user.is_eadu_user: # + we could check that they correspond
            raise
        if not eadu_contact.parent_id:
            raise
        eadu_contact.sudo().write({
            'eadu_url': url,
            'eadu_apikey': apikey,
            'eadu_db': db,
        })
        # We already sync the users that did the exchange, so they 
        # can already talk to each other
        ypartnerreturn = eadu_contact.sudo()._create_child_contact(cusername, int(cpartnereadu))
        ypartner = self.env['res.partner'].sudo().browse(int(ypartnerid))
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_contact, 'res.partner', ypartner, ypartnereadu)
        return {'result': ypartnerreturn.id}

    def action_eadu_create_contact(self, eadu_ident, name, email):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise
        partner = eadu_contact._create_child_contact(name, eadu_ident)
        partner.email = email
        return {'result': partner.id}      

    def _eadu_call(self, model, method, params):
        """
        self = eaducontact
        """
        self.ensure_one()
        url = f"{self.eadu_url}/json/2/{model}/{method}"
        print("URL", url)
        result = requests.post(
            url,
            headers={
                "X-Odoo-Database": self.eadu_db,
                "Authorization": f"bearer {self.eadu_apikey}",
            },
            json=params,
        ).json()
        # TODO: error handling and stuff

        return result

    @api.readonly
    @api.model
    def _search_for_channel_invite(self, store: Store, search_term, channel_id=None, limit=30):
        res = super()._search_for_channel_invite(store, search_term, channel_id=channel_id, limit=limit)
        partner_ids = self.env['eadu.partner.any'].sudo().search([('res_model', '=', 'res.partner')]).mapped('res_id')
        partners = self.browse(partner_ids)
        partners.filtered(lambda p: not p.user_ids)
        channel = self.env["discuss.channel"]
        if channel_id:
            channel = channel.browse(channel_id)
        partners._search_for_channel_invite_to_store(store, channel)
        return {
            "count": len(partners) + res['count'],
            "partner_ids": res['partner_ids'] + partners.ids,
        }
    
    def write(self, vals):
        res = super().write(vals)
        for partner in self:
            self.env['eadu.partner.any'].sudo()._sync_with_others('res.partner', partner.id, vals)
        return res