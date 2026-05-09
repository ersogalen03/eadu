# Part of Eadu. See LICENSE file for full copyright and licensing details.

import requests
import base64
import odoo
import threading
import logging

from odoo import api, fields, models, Command, _
from odoo.addons.mail.tools.discuss import Store

from odoo.addons.eadu.exceptions import EaduConnectionError

_logger = logging.getLogger(__name__)


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
        partner_company_id = partner.company_id.id
        user_company_id = partner_company_id or self.env.company.id
        user_company_ids = [user_company_id]
        eadu_contact = partner.child_ids.filtered(lambda p: p.eadu_url)
        if not eadu_contact:
            eadu_contact = self.env['res.partner'].search([('name', '=', "EADU" + partner.name), ('parent_id', '=', partner.id)], limit=1)
            if not eadu_contact:
                eadu_contact = self.env['res.partner'].create({
                    'name': "EADU" + partner.name,
                    'parent_id': partner.id,
                    'company_type': 'person',
                    'type': 'other', 
                    'company_id': partner_company_id,
                })
            eadu_user = self.env['res.users'].search([
                ('partner_id', '=', eadu_contact.id),
                ('group_ids', 'in', [self.env.ref('eadu.group_portal_eadu').id]),
            ])
            if not eadu_user:        
                eadu_user = self.env['res.users'].create({
                    'partner_id': eadu_contact.id,
                    'login': "EADU" + self.name.strip().strip('#'),
                    'group_ids': [Command.link(self.env.ref('eadu.group_portal_eadu').id)],
                    'company_id': user_company_id,
                    'company_ids': [Command.set(user_company_ids)],
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
        partner_company_id = partner.company_id.id
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
                'company_id': partner_company_id,
            })
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(self, 'res.partner', child_partner.id, eadu_ident)
        return child_partner

    def _generate_eadu_exchange(self):
        """ Generate an exchange token for linking 2 companies"""        
        self.ensure_one()
        # Check if there is a child partner which is linked to an is_eadu user
        eadu_contact = self._create_update_eadu_child_partner()
        api_key = eadu_contact._generate_eadu_key()

        web_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        dbname = self._get_db_name()
        cuser = self.env.user # To already create yourself in the other db (if you have not been already)
        return base64.b64encode('#'.join([web_url, api_key, dbname, cuser.name, str(cuser.partner_id.id)]).encode()).decode()

    def button_generate_eadu_exchange(self):
        exch = self._generate_eadu_exchange()
        self.env.cr.commit()

        raise odoo.exceptions.UserError(
            _("Copy/paste and tell your contact to use the following code on the partner form of you in his Odoo instance:") + "\n" 
            + exch
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
        if not user.has_group('eadu.group_portal_eadu'): # + we could check that they correspond
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
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_contact, 'res.partner', ypartner.id, ypartnereadu)
        return {'result': ypartnerreturn.id}

    def action_eadu_create_contact(self, eadu_ident, name, email, eadu_url=None, eadu_url_ident=None):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise

        # When eadu_url is provided, check if we already have a connection to that DB
        # and whether the partner already exists (avoids duplicates for multi-DB channels)
        if eadu_url and eadu_url_ident:
            other_eadu_contact = self.env['res.partner'].sudo().search([
                ('eadu_url', '=', eadu_url),
            ], limit=1)
            if other_eadu_contact:
                epa = self.env['eadu.partner.any'].sudo().search([
                    ('partner_id', '=', other_eadu_contact.id),
                    ('res_model', '=', 'res.partner'), 
                    ('eadu_ident', '=', eadu_url_ident),
                ], limit=1)
                if epa:
                    #epa._get_record().email = email
                    epa._search_create_for_eadu_partner(eadu_contact, 'res.partner', epa.res_id, eadu_ident, partner_master=True)
                    return {'result': epa.res_id}

                
        partner = eadu_contact._create_child_contact(name, eadu_ident)
        partner.email = email
        return {'result': partner.id}

    def _eadu_call(self, model, method, params):
        """Make a synchronous JSON RPC call to the remote eadu instance.

        Raises ``EaduConnectionError`` on any network or HTTP-level failure so that
        callers can route the call through the queue instead of crashing.
        """
        self.ensure_one()
        url = f"{self.eadu_url}/json/2/{model}/{method}"
        try:
            response = requests.post(
                url,
                headers={
                    "X-Odoo-Database": self.eadu_db,
                    "Authorization": f"bearer {self.eadu_apikey}",
                },
                json=params,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            _logger.exception(
                "eadu: _eadu_call to %s failed with exception", url
            )
            if isinstance(exc, requests.exceptions.Timeout):
                raise EaduConnectionError(f"Timeout calling {url}") from exc
            if isinstance(exc, requests.exceptions.ConnectionError):
                raise EaduConnectionError(f"Connection error calling {url}") from exc
            if isinstance(exc, requests.exceptions.HTTPError):
                raise EaduConnectionError(f"HTTP error calling {url}: {exc.response.status_code} {exc.response.reason}") from exc
            raise EaduConnectionError(f"Request exception calling {url}") from exc

    @api.readonly
    @api.model
    def _search_for_channel_invite(self, store: Store, search_term, channel_id=None, limit=30):
        res = super()._search_for_channel_invite(store, search_term, channel_id=channel_id, limit=limit)
        partner_ids = self.env['eadu.partner.any'].sudo().search([('res_model', '=', 'res.partner')]).mapped('res_id')
        partners = self.browse(partner_ids)
        partners = partners.filtered(lambda p: not p.user_ids)

        channel = self.env["discuss.channel"]
        if channel_id:
            channel = channel.browse(channel_id)
        
        partners._search_for_channel_invite_to_store(store, channel) 
        partner_ids = list(set(res['partner_ids'] + partners.ids))
        return {
            "count": len(partner_ids),
            "partner_ids": partner_ids,
        }
    
    def _eadu_ensure_remote_partner(self, eadu_partner):
        """Ensure this partner exists on the remote instance for eadu_partner.

        Returns the ``eadu.partner.any`` record for this mapping.  When the remote
        call cannot be sent immediately (connection blocked), a placeholder record
        with ``eadu_ident=0`` is returned and the call is queued for retry.
        Returns ``False`` only when the partner cannot be associated at all.
        """
        self.ensure_one()
        EaduAny = self.env['eadu.partner.any'].sudo()
        epu = EaduAny._search_for_eadu_partner(eadu_partner, 'res.partner', self.id)
        if epu:
            return epu  # Already mapped (eadu_ident may be 0 if still queued).

        params = {
            'name': self.name,
            'email': self.email,
            'eadu_ident': self.id,
        }
        # If this partner belongs to another EADU instance, pass the cross-reference
        # so the remote can deduplicate.
        partner_corresponding_eadu = self._get_eadu_partner()
        if partner_corresponding_eadu:
            epp = EaduAny._search_for_eadu_partner(
                partner_corresponding_eadu, 'res.partner', self.id
            )
            if epp:
                params['eadu_url_ident'] = epp.eadu_ident
            params['eadu_url'] = partner_corresponding_eadu.eadu_url

        return EaduAny._send_or_queue(
            eadu_partner,
            'res.partner',
            'action_eadu_create_contact',
            params,
            local_model='res.partner',
            local_res_id=self.id,
            result_key='result',
            partner_master=False,
        )

    # def write(self, vals):
    #     res = super().write(vals)
    #     for partner in self:
    #         self.env['eadu.partner.any'].sudo()._sync_with_others('res.partner', partner.id, vals)
    #     return res