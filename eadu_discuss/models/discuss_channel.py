# Part of Eadu. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    eadu_ident = fields.Integer('Eadu Identification', copy=False)
