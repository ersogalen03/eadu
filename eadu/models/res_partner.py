# Part of Eadu. See LICENSE file for full copyright and licensing details.

import json
import requests
import uuid
import base64

from odoo import api, fields, models
from odoo.addons.iap.tools.iap_tools import iap_jsonrpc as jsonrpc



class ResPartner(models.Model):
    _inherit = "res.partner"

    eadu_url = fields.Char("Other Instance URL", copy=False)
    eadu_login = fields.Char("Other Instance Login", copy=False)
    eadu_password = fields.Char("Other Instance Password", copy=False, 
                                groups="base.group_system")
    eadu_exchange = fields.Char('Token To Exchange')
    eadu_exchange_date = fields.Datetime('Exchange Date')

    eadu_exchanged = fields.Char('Token Exchanged')


    def button_generate_eadu_exchange(self):
        self.ensure_one()
        # Check if there is a child partner which is linked to an is_eadu user
        child_partner = self.child_ids.filtered(lambda p: p.user_ids.is_eadu_user)
        if not child_partner:
            child_partner = self.env['res.partner'].create({
                'name': "EADU" + self.name,
                'parent_id': self.id,
            })
            user = self.env['res.users'].create({
                'partner_id': self.id,
                'login': "EADU" + self.name.strip().strip('#'),
                'is_eadu_user': True,
                'group_id': self.env.ref('base.group_portal').id,
                'password': uuid.uuid4().hex,
            })
        else:
            user = child_partner.user_ids.filtered(lambda u: u.eadu_ident)
        web_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        
        self.eadu_exchange = base64.b64encode('#'.join([web_url, user.login, user.password()]))
        self.eadu_exchange_date = fields.Datetime.now()

    def button_process_eadu_exchanged(self):
        self.ensure_one()
        # Decode the exchange token
        con_str = base64.b64decode(self.eadu_exchanged).decode()
        con_arr = con_str.split('#')
        self.eadu_url = con_arr[0]
        self.eadu_login = con_arr[1]
        self.eadu_password = con_arr[2]

        # Check if the partner is already linked to an EADU user
        if self.child_ids.filtered(lambda p: p.user_ids.is_eadu_user):
            raise ValueError("A child partner is already linked to an EADU user.")

        # Create a new child partner and user
        child_partner = self.env['res.partner'].create({
            'name': "EADU" + self.name,
            'parent_id': self.id,
        })
        user = self.env['res.users'].create({
            'partner_id': child_partner.id,
            'login': "EADU" + self.name.strip().strip('#'),
            'is_eadu_user': True,
            'group_id': self.env.ref('base.group_portal').id,
            'password': uuid.uuid4().hex,
        })
        self._eadu_login()
        self._eadu_call('eadu/1/connecteadu', self.eadu_url, {
            'login': user.login,
            'password': user.password,
            'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
        })

    def _eadu_call(self, url_ext, params):
        self.ensure_one()
        params.update({
            'partner_ident': self.eadu_ident,
        })
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
            }
        }

        # Headers
        headers = {"Content-Type": "application/json"}

        # Send login request
        session = requests.Session()
        response = session.post(login_url, data=json.dumps(payload), headers=headers)




    def button_eadu_connect(self):
        self.ensure_one()
        self._eadu_login()
        self.env['res.users'].create({
            'partner_id': self.id,
            'is_eadu_user': True,

            'eadu_ident': self.eadu_ident,
        })

        self._eadu_call('eadu/1/connecteadu', {
            'username': self.eadu_login,
            'password': self.eadu_password,
            'token': self.env.user.eadu_token,
            'url': self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        })

