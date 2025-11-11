from odoo import models, fields, api,_
from odoo.http import request


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    payment_required_first = fields.Boolean(
        string="Payment Required First",
        help="Check this if a pending request should be prioritized first."
    )

class ContractType(models.Model):
    _inherit = 'contract.type'

    with_parts = fields.Boolean("With Parts")
