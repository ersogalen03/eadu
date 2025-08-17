# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import http, _
from odoo.http import request
from odoo import Command

import json


class TermsController(http.Controller):

    # @http.route('/eadu/1/connecteadu', type='json', auth='user')
    # def connect_eadu(self, username, password, token, url):
    #     """ 1 portal user is granted a token for Eadu access
    #     In the db that owns that user the user will put the credentials
    #     Then when connecting to the db, it will create a portal user himself
    #     for which it sends the credentials and it sends its own url
    #       """
    #     user = self.env.user
    #     partner = user.partner_id.parent_id
    #     if user.eadu_token != token:
    #         raise
    #     partner.write({
    #         'eadu_url': url,
    #         'eadu_login': username,
    #         'eadu_password': password,
    #     })
    #     user.is_eadu_user = True
    #     # TODO: it could replace the portal user of the original db with the new one through the return
    #     # That way we would really have the EADU portal users
        
        
    #     return True

    @http.route('/eadu/1/connecteadu', type='json', auth='user')
    def connect_eadu(self, login, password, url, db, cuserid, cusername):
        user = request.env.user
        partner = user.partner_id.parent_id
        if not user.is_eadu_user:
            raise
        if partner:
            partner.write({
                'eadu_url': url,
                'eadu_login': login,
                'eadu_password': password,
                'eadu_db': db,
            })
        partner.sudo().create({
            'parent_id': partner.id,
            'name': cusername,
            'eadu_ident': cuserid,
        })


    @http.route('/eadu/1/messagereceive', type='json', auth='user')
    def message_receive_u(self, user_id, model, res_id, body): # What about the user_id?
        user = request.env.user
        partner = user.partner_id.parent_id
        if not user.is_eadu_user:
            raise

        message_user = request.env['res.users'].sudo().browse(user_id)
        vals = {
            'body': body,
            'author_id': user.partner_id.id,
            'res_id': res_id,
            'model': model,
        }
        message = request.env['mail.message'].with_context(eadu_message=True).sudo().create(vals)

        return {'message_id': message.id}


    @http.route('/eadu/1/usercreateu', type='json', auth='public')
    def user_create_u(self, eadu_ident, name):
        partner = request.env['res.partner'].sudo().browse(partner_ident)
        if not partner.eadu_ident:
            raise
        vals = {
            'name': name + ' ' + partner.name,
            'login': name + '_' + partner.name,
            'groups_id': [Command.link(request.env.ref('base.group_portal').id)],
            'eadu_ident': eadu_ident,
        }
        
        user = request.env['res.users'].sudo().create(vals)
        user.partner_id.parent_id = partner
        return {'user_id': user.id}



    @http.route('/eadu/1/messagereceive', type='json', auth='public')
    def message_receive(self, partner_ident, user_id, model, res_id, body):
        partner = request.env['res.partner'].sudo().browse(partner_ident)
        if not partner.eadu_ident:
            raise
        user = request.env['res.users'].sudo().browse(user_id)

        vals = {
            'body': body,
            'author_id': user.partner_id.id,
            'res_id': res_id,
            'model': model,
        }
        message = request.env['mail.message'].with_context(eadu_message=True).sudo().create(vals)

        return {'message_id': message.id}

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
        
        user = request.env['res.users'].sudo().create(vals)
        user.partner_id.parent_id = partner
        return {'user_id': user.id}
