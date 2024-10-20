# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    eadu_url = fields.Char("Other Instance URL")
    eadu_ident = fields.Integer("Other Instance ID")
