# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResPartner(models.Model):
    _name = "eadu.partner.user"
    _description = "Link between Eadu Partners and users"

    partner_id = fields.Many2one('res.partner', 'Eadu Partner')
    user_id = fields.Many2one('res.users')
    eadu_ident = fields.Integer('Eadu Identification')
    