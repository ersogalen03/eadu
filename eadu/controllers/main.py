# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request
from odoo import Command

import json


class TermsController(http.Controller):


    @http.route('/eadu/1/connecteadu', type='json', auth='user')
    def connect_eadu(self, login, password, url, db, cuserid, cusername, ypartnerid, yuserid):
        """
            cuser: current user of the caller
            yuser: user that asked for the exchange in this db
            ypartner: equivalent of yuser in the caller db
        """
        user = request.env.user
        partner = user.partner_id.parent_id
        if not user.is_eadu_user:
            raise
        if not partner:
            raise
        partner.write({
            'eadu_url': url,
            'eadu_login': login,
            'eadu_password': password,
            'eadu_db': db,
        })
        # TODO: add and check tokens
        ypartnerreturn = partner.sudo().create({
            'parent_id': partner.id,
            'name': cusername,
            'eadu_ident': cuserid,
        })
        yuseruser = request.env['res.partner'].sudo().browse(int(yuserid)).user_ids[0].id
        request.env['eadu.partner.any'].sudo()._search_create_for_eadu_partner(partner, 'res.users', yuseruser, ypartnerid)
        return ypartnerreturn.id


    @http.route('/eadu/1/messagereceive', type='json', auth='user')
    def message_receive(self, model, res_id, body, user_id):
        user = request.env.user
        partner = user.partner_id.parent_id
        if not partner.eadu_url:
            raise

        vals = {
            'body': body,
            'author_id': user_id,
            'res_id': res_id,
            'model': model,
        }
        if model == 'discuss.channel':
            channel = request.env['discuss.channel'].sudo().browse(res_id)
            message = channel.with_context(eadu_message=True).message_post(author_id= user_id, body=body, message_type='comment', subtype_xmlid='mail.mt_comment')
        else:
            message = request.env['mail.message'].with_context(eadu_message=True).sudo().create(vals)

        return {'message_id': message.id}

    @http.route('/eadu/1/contactcreate', type='json', auth='user')
    def contact_create(self, eadu_ident, name, email):
        user = request.env.user
        partner = user.partner_id.parent_id
        if not partner.eadu_url:
            raise
        vals = {
            'name': name + ' ' + partner.name,
            'eadu_ident': eadu_ident,
            'email': email,
            'parent_id': partner.id, # TODO: some images and stuff
        }
        created_partner = request.env['res.partner'].sudo().create(vals)
        return created_partner.id

    @http.route('/eadu/1/channelcreate', type='json', auth='user')
    def channel_create(self, name, eadu_ident, partner_ids):
        """
        Create a new mail.channel with the given name and partners.
        partner_ids: list of res.partner IDs to add to the channel (required).
        """
        user = request.env.user
        if not user.partner_id.parent_id.eadu_url:
            raise
        if not partner_ids or not isinstance(partner_ids, list):
            raise
        # Ensure the current user's partner is always included
        #if user.partner_id.id not in partner_ids:
        #    partner_ids.append(user.partner_id.id)

        partners = request.env['res.partner'].sudo().browse(partner_ids)
        for partner in partners:
            if partner.user_ids:
                puser = partner.user_ids[0]

        vals = {
            'name': name,
            'channel_partner_ids': [(4, x) for x in partner_ids],
            'channel_type': 'chat',
            'eadu_ident': eadu_ident,
        }
        print(vals)
        channel = request.env['discuss.channel'].sudo().with_user(puser).create(vals)
        return {'channel_id': channel.id}
    
    @http.route('/eadu/1/usercreate', type='json', auth='public')
    def user_create(self, partner_ident, eadu_ident, name):
        partner = request.env['res.partner'].sudo().browse(partner_ident)
        if not partner.eadu_ident:
            raise
        vals = {
            'name': name + ' ' + partner.name,
            'login': name + '_' + partner.name,
            'groups_id': [Command.link(request.env.ref('base.group_portal').id)],
            'eadu_ident': eadu_ident,
        }
        
        user = request.env['res.users'].sudo().with_context(install_mode=True).create(vals)
        user.partner_id.parent_id = partner
        return {'user_id': user.id}
