# Part of Eadu. See LICENSE file for full copyright and licensing details.

import requests
import base64
import binascii
import odoo
import threading
import logging

from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, UserError
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
    eadu_connection_partner_id = fields.Many2one(
        "res.partner",
        string="EADU Contact",
        compute="_compute_eadu_connection",
    )
    eadu_connection_url = fields.Char(
        string="EADU URL",
        compute="_compute_eadu_connection",
    )
    eadu_connection_db = fields.Char(
        string="EADU Database",
        compute="_compute_eadu_connection",
    )
    has_eadu_partner = fields.Boolean(
        string="Has EADU Partner",
        compute="_compute_has_eadu_partner",
        store=True,
    )

    def _check_eadu_exchange_access(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only users with Settings access can manage EADU connections."))

    def _compute_eadu_connection(self):
        for partner in self:
            eadu_contact = partner.commercial_partner_id.child_ids.filtered("eadu_url")[:1]
            partner.eadu_connection_partner_id = eadu_contact
            partner.eadu_connection_url = eadu_contact.eadu_url
            partner.eadu_connection_db = eadu_contact.eadu_db

    @api.depends("commercial_partner_id.child_ids.eadu_url")
    def _compute_has_eadu_partner(self):
        for partner in self:
            partner.has_eadu_partner = bool(partner.commercial_partner_id.child_ids.filtered("eadu_url"))

    def _get_eadu_partner(self):
        self.ensure_one()
        return self.eadu_connection_partner_id

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
        eadu_contact = partner.eadu_connection_partner_id
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
                    'login': self._get_unique_eadu_user_login(),
                    'group_ids': [Command.link(self.env.ref('eadu.group_portal_eadu').id)],
                    'company_id': user_company_id,
                    'company_ids': [Command.set(user_company_ids)],
                })
        return eadu_contact

    def _get_unique_eadu_user_login(self):
        self.ensure_one()
        base_login = "EADU" + (self.name or "").strip().strip('#')
        login = base_login
        index = 2
        Users = self.env['res.users'].sudo()
        while Users.search_count([('login', '=', login)]):
            login = f"{base_login}-{index}"
            index += 1
        return login
 
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
        child_partner = partner.child_ids.filtered(lambda p: p.name == name)[:1]
        eadu_rec = self.env['eadu.partner.any']
        if child_partner:
            eadu_rec = self.env['eadu.partner.any'].sudo()._search_for_eadu_partner(
                self, 'res.partner', child_partner.id
            )
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
        self._check_eadu_exchange_access()
        # Check if there is a child partner which is linked to an is_eadu user
        eadu_contact = self._create_update_eadu_child_partner()
        api_key = eadu_contact._generate_eadu_key()

        web_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        dbname = self._get_db_name()
        cuser = self.env.user # To already create yourself in the other db (if you have not been already)
        exchange_partner = self._eadu_exchange_company_partner(cuser)
        return base64.b64encode('#'.join([
            web_url,
            api_key,
            dbname,
            cuser.name,
            str(cuser.partner_id.id),
            exchange_partner.name,
        ]).encode()).decode()

    def _eadu_exchange_company_partner(self, user=None):
        user = user or self.env.user
        return user.company_id.partner_id or user.partner_id.commercial_partner_id

    @api.model
    def _decode_eadu_exchange_token(self, token):
        try:
            con_str = base64.b64decode(token.strip().encode(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError, AttributeError):
            raise UserError(_("This EADU code is not valid."))

        con_arr = con_str.split('#', 5)
        if len(con_arr) < 5:
            raise UserError(_("This EADU code is incomplete."))
        return {
            'url': con_arr[0],
            'apikey': con_arr[1],
            'db': con_arr[2],
            'user_name': con_arr[3],
            'partner_id': con_arr[4],
            'partner_name': con_arr[5] if len(con_arr) > 5 else False,
        }

    def _process_eadu_exchange_token(self, token):
        self.ensure_one()
        self._check_eadu_exchange_access()
        return self.sudo()._process_eadu_exchange_token_sudo(token, self.env.user)

    def _process_eadu_exchange_token_from_portal(self, token, portal_user):
        self.ensure_one()
        if not portal_user or portal_user._is_public():
            raise AccessError(_("You must be logged in to connect EADU."))
        if not portal_user.has_group("base.group_portal"):
            raise AccessError(_("Only portal users can connect EADU from the portal."))

        portal_partner = portal_user.partner_id.commercial_partner_id
        if portal_partner.id != self.commercial_partner_id.id:
            raise AccessError(_("You can only connect EADU for your own company."))

        return self.sudo()._process_eadu_exchange_token_sudo(token, portal_user)

    def _process_eadu_exchange_token_sudo(self, token, connecting_user=None):
        self.ensure_one()
        con_data = self._decode_eadu_exchange_token(token)

        eadu_contact = self._create_update_eadu_child_partner()
        eadu_contact.eadu_url = con_data['url']
        eadu_contact.eadu_apikey = con_data['apikey']
        eadu_contact.eadu_db = con_data['db']
        apikey = eadu_contact._generate_eadu_key()
        try:
            remote_partner_id = int(con_data['partner_id'])
        except (TypeError, ValueError):
            raise UserError(_("This EADU code contains an invalid partner identifier."))
        connecting_contact = eadu_contact._create_child_contact(
            con_data['user_name'],
            remote_partner_id,
        )
        cuser = connecting_user or self.env.user
        company_partner = self._eadu_exchange_company_partner(cuser)
        company_partner_fields = company_partner._eadu_prepare_update_fields() if company_partner else {}
        res = eadu_contact._eadu_call('res.partner', 'action_connect_eadu', {
            'apikey': apikey,
            'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
            'db': self._get_db_name(),
            'cusername': cuser.name,
            'cpartnereadu': cuser.partner_id.id,
            'ypartnereadu': connecting_contact.id, # newly created partner in this db
            'ypartnerid': remote_partner_id, # for the contacted db to verify who started it originally
            'ycompanyeadu': self.commercial_partner_id.id,
            'fields': {
                'partner': company_partner_fields,
                'company_partner': company_partner_fields,
                'user_partner': cuser.partner_id._eadu_prepare_update_fields(),
            },
        })
        ypartnerid = res['result']
        if ypartnerid:
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(self, 'res.users', cuser.id, ypartnerid)
            user_partner_any = self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                eadu_contact, 'res.partner', cuser.partner_id.id, ypartnerid
            )
            cuser.partner_id._eadu_send_update(user_partner_any)
        if res.get('company_result'):
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                eadu_contact, 'res.partner', self.commercial_partner_id.id,
                res['company_result'], partner_master=True,
            )
        response_fields = res.get('fields') or {}
        partner_fields = self._eadu_merge_field_values(
            response_fields.get('partner'),
            response_fields.get('company_partner'),
        )
        partner_vals = self._eadu_update_values_from_fields(partner_fields)
        if partner_vals:
            self.with_context(eadu_message=True).write(partner_vals)
        user_partner_vals = self._eadu_update_values_from_fields(response_fields.get('user_partner'))
        if user_partner_vals:
            connecting_contact.with_context(eadu_message=True).write(user_partner_vals)

    def action_open_eadu_exchange_wizard(self):
        self.ensure_one()
        self._check_eadu_exchange_access()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Connect EADU"),
            'res_model': 'eadu.exchange.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.commercial_partner_id.id,
                'active_model': 'res.partner',
                'active_id': self.commercial_partner_id.id,
            },
        }

    def button_generate_eadu_exchange(self):
        exch = self._generate_eadu_exchange()
        self.env.cr.commit()

        raise odoo.exceptions.UserError(
            _("Copy/paste and tell your contact to use the following code on the partner form of you in his Odoo instance:") + "\n" 
            + exch
        )

    def button_process_eadu_exchanged(self):
        self.ensure_one()
        self._process_eadu_exchange_token(self.eadu_exchanged)

    def action_connect_eadu(
        self, apikey, url, db, cusername, cpartnereadu, ypartnereadu, ypartnerid,
        ycompanyeadu=None, fields=None, company_fields=None, user_partner_fields=None,
    ):
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
        fields = fields or {}
        ypartnerreturn = eadu_contact.sudo()._create_child_contact(cusername, int(cpartnereadu))
        user_partner_vals = self._eadu_update_values_from_fields(
            fields.get('user_partner') or user_partner_fields
        )
        if user_partner_vals:
            ypartnerreturn.with_context(eadu_message=True).write(user_partner_vals)
        ypartner = self.env['res.partner'].sudo().browse(int(ypartnerid))
        self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(eadu_contact, 'res.partner', ypartner.id, ypartnereadu)
        if ycompanyeadu:
            self.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(
                eadu_contact, 'res.partner', eadu_contact.commercial_partner_id.id,
                int(ycompanyeadu),
            )
        company_vals = self._eadu_update_values_from_fields(
            self._eadu_merge_field_values(
                fields.get('partner') or company_fields,
                fields.get('company_partner'),
            )
        )
        if company_vals:
            eadu_contact.commercial_partner_id.with_context(eadu_message=True).write(company_vals)
        company_partner = self._eadu_exchange_company_partner(user)
        company_partner_fields = company_partner._eadu_prepare_update_fields() if company_partner else {}
        return {
            'result': ypartnerreturn.id,
            'company_result': company_partner.id,
            'fields': {
                'partner': company_partner_fields,
                'company_partner': company_partner_fields,
                'user_partner': ypartner._eadu_prepare_update_fields(),
            },
        }

    def _eadu_merge_field_values(self, *fields_values):
        """Merge field payloads, using later payloads only for empty values."""
        vals = {}
        for payload in fields_values:
            for field_name, value in (payload or {}).items():
                if field_name not in vals or not vals[field_name]:
                    vals[field_name] = value
        return vals

    def _eadu_update_values_from_fields(self, fields_values):
        if not fields_values:
            return {}
        allowed_fields = set(self._eadu_update_field_names())
        vals = {}
        for field_name, value in fields_values.items():
            if field_name in allowed_fields and field_name in self._fields:
                vals[field_name] = value
        return vals

    def _eadu_update_field_names(self):
        return [
            'name', 'email', 'phone', 'mobile', 'function', 'street',
            'street2', 'zip', 'city', 'state_id', 'country_id',
            'company_type', 'is_company', 'type', 'website', 'vat',
            'company_registry', 'title', 'lang', 'tz', 'image_1920',
        ]

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
                'res.partner',
                'action_eadu_update',
                {
                    'eadu_ident': eadu_any.res_id,
                    'fields': vals,
                },
                eadu_any_ref=eadu_any,
            )

    def action_eadu_create_contact(self, eadu_ident, name, email, eadu_url=None, eadu_url_ident=None, fields=None):
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
                    vals = self._eadu_update_values_from_fields(fields)
                    if vals:
                        epa._get_record().sudo().with_context(eadu_message=True).write(vals)
                    return {'result': epa.res_id}

                
        partner = eadu_contact._create_child_contact(name, eadu_ident)
        vals = self._eadu_update_values_from_fields(fields)
        vals.setdefault('email', email)
        partner.sudo().with_context(eadu_message=True).write(vals)
        return {'result': partner.id}

    def action_eadu_update(self, eadu_ident, fields):
        eadu_contact = self.env.user.partner_id
        if not eadu_contact.eadu_url:
            raise
        eadu_any = self.env['eadu.partner.any'].sudo()._search_for_eadu_ident(
            eadu_contact, 'res.partner', eadu_ident
        )
        if not eadu_any:
            return {'result': False}
        vals = self._eadu_update_values_from_fields(fields)
        if vals:
            eadu_any._get_record().sudo().with_context(eadu_message=True).write(vals)
        return {'result': True}

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
            'fields': self._eadu_prepare_update_fields(),
        }
        # If this partner belongs to another EADU instance, pass the cross-reference
        # so the remote can deduplicate.
        partner_corresponding_eadu = self.eadu_connection_partner_id
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

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('eadu_message'):
            changed_fields = set(vals) & set(self._eadu_update_field_names())
            if not changed_fields:
                return res
            EaduAny = self.env['eadu.partner.any'].sudo()
            for partner in self:
                eadu_anys = EaduAny.search([
                    ('res_model', '=', 'res.partner'),
                    ('res_id', '=', partner.id),
                ])
                for eadu_any in eadu_anys:
                    partner._eadu_send_update(eadu_any, changed_fields)
        return res
