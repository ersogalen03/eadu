# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    eadu_ident = fields.Integer("Other Instance ID", copy=False)