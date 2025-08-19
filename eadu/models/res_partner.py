# Part of Eadu. See LICENSE file for full copyright and licensing details.

import json
import requests
import uuid
import base64
import odoo
import threading

from odoo import api, fields, models, Command, _
from odoo.addons.iap.tools.iap_tools import iap_jsonrpc as jsonrpc



class ResPartner(models.Model):
    _inherit = "res.partner"

    # Linking 2 companies
    eadu_url = fields.Char("Other Instance URL", copy=False)
    eadu_login = fields.Char("Other Instance Login", copy=False)
    eadu_password = fields.Char("Other Instance Password", copy=False, 
                                groups="base.group_system")
    eadu_db = fields.Char("Other Instance DB", copy=False)
    eadu_exchange_token = fields.Char('Token To Exchange', copy=False)
    eadu_exchange_date = fields.Datetime('Exchange Date', copy=False)

    # For individuals of the contact company
    eadu_ident = fields.Integer("Other Instance ID", copy=False) # For contacts

    # This should be in a wizard instead
    eadu_exchanged = fields.Char('Token Exchanged', copy=False)


    def _get_db_name(self):
        db = odoo.tools.config['db_name']
        # If the database name is not provided on the command-line,
        # use the one on the thread (which means if it is provided on
        # the command-line, this will break when installing another
        # database from XML-RPC).
        if not db and hasattr(threading.current_thread(), 'dbname'):
            return threading.current_thread().dbname
        return db

    def button_generate_eadu_exchange(self):
        self.ensure_one()
        # Check if there is a child partner which is linked to an is_eadu user
        child_partner = self.child_ids.filtered(lambda p: p.user_ids.is_eadu_user)
        password = uuid.uuid4().hex
        if not child_partner:
            child_partner = self.env['res.partner'].create({
                'name': "EADU" + self.name,
                'parent_id': self.id,
            })
            
            user = self.env['res.users'].create({
                'partner_id': child_partner.id,
                'login': "EADU" + self.name.strip().strip('#'),
                'is_eadu_user': True,
                'groups_id': [Command.link(self.env.ref('base.group_portal').id)],
                'password': password,
            })
        else:
            user = child_partner.user_ids.filtered(lambda u: u.is_eadu_user)
            user.password = password

        web_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        dbname = self._get_db_name()
        self.eadu_exchange_date = fields.Datetime.now()
        self.eadu_exchange_token = uuid.uuid4() # TODO: use it in the return stuff
        self.env.cr.commit()
        cuser = self.env.user
        raise odoo.exceptions.UserError(
            _("Copy/paste and tell your contact to use the following code on the partner form of you in his Odoo instance:") + "\n" 
            + base64.b64encode('#'.join([web_url, user.login, password, dbname, cuser.name, str(cuser.id)]).encode()).decode()
        )
        

    def button_process_eadu_exchanged(self):
        self.ensure_one()
        # Decode the exchange token
        con_str = base64.b64decode(self.eadu_exchanged.encode()).decode()
        con_arr = con_str.split('#')
        self.eadu_url = con_arr[0]
        self.eadu_login = con_arr[1]
        self.eadu_password = con_arr[2]
        self.eadu_db = con_arr[3]


        # Check if the partner is already linked to an EADU user
        child_partner = self.child_ids.filtered(lambda p: p.user_ids.is_eadu_user)
        password = uuid.uuid4().hex
        if not child_partner:
            # Create a new child partner and user
            child_partner = self.env['res.partner'].create({
                'name': "EADU" + self.name,
                'parent_id': self.id,
            })
            user = self.env['res.users'].create({
                'partner_id': child_partner.id,
                'login': "EADU" + self.name.strip().strip('#'),
                'is_eadu_user': True,
                'groups_id': [Command.link(self.env.ref('base.group_portal').id)],
                'password': password,
            })
        else:
            user = child_partner.user_ids.filtered(lambda u: u.is_eadu_user)
            user.password = password

        child_partner = self.child_ids.filtered(lambda p: p.name == con_arr[4] or p.eadu_ident == con_arr[5])
        if child_partner:
            if not child_partner.eadu_ident or child_partner.eadu_ident != con_arr[5]:
                child_partner.eadu_ident = con_arr[5]
            if child_partner.name != con_arr[4]:
                child_partner.name = con_arr[4]
        else:
            child_partner = self.env['res.partner'].create({
                'name': con_arr[4],
                'eadu_ident': con_arr[5],
                'parent_id': self.id,
            })

        cuser = self.env.user
        session = self._eadu_login()
        response = self._eadu_call('/eadu/1/connecteadu', {
            'login': user.login,
            'password': password,
            'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
            'db': self._get_db_name(),
            'cusername': cuser.name,
            'cuserid': cuser.id,
            'ypartnerid': child_partner.id,
            'yuserid': con_arr[5],
        }, session=session)
        res = response.json()
        ypartnerid = res['result']
        if ypartnerid:
            epu = self.env['eadu.partner.user'].sudo().search([('partner_id', '=', self.id), ('user_id', '=', cuser.id)])
            if epu and epu.eadu_ident != ypartnerid:
                epu.eadu_ident = ypartnerid
            elif not epu:
                self.env['eadu.partner.user'].sudo().create({
                    'partner_id': self.id,
                    'user_id': cuser.id,
                    'eadu_ident': ypartnerid,
                })

    def _eadu_call(self, url_ext, params, session=None):
        self.ensure_one()
        result = False
        if session:
            payload = {
                'jsonrpc': '2.0',
                'method': 'call',
                'params': params,
                'id': uuid.uuid4().hex,
            }
            result = session.post(self.eadu_url + url_ext, json=payload, timeout=15)
        else:
            result = jsonrpc(self.eadu_url + url_ext, params=params)
        return result

    def _eadu_login(self):
        login_url = f"{self.eadu_url}/web/session/authenticate"

        # Login payload
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                # TODO: check if we need db
                "login": self.eadu_login,
                "password": self.eadu_password,
                "db": self.eadu_db,
            }
        }

        # Headers
        headers = {"Content-Type": "application/json"}

        # Send login request
        session = requests.Session()
        response = session.post(login_url, data=json.dumps(payload), headers=headers)
        return session




    # def button_eadu_connect(self):
    #     self.ensure_one()
    #     self._eadu_login()
    #     self.env['res.users'].create({
    #         'partner_id': self.id,
    #         'is_eadu_user': True,

    #         'eadu_ident': self.eadu_ident,
    #     })

    #     self._eadu_call('eadu/1/connecteadu', {
    #         'username': self.eadu_login,
    #         'password': self.eadu_password,
    #         'token': self.env.user.eadu_token,
    #         'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url')
    #     })

