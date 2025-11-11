from odoo import models,fields,api


class ResCompany(models.Model):
    _inherit = 'res.company'

    enable_warehouse = fields.Selection([('external_warehouse','External Warehouse'),('internal_warehouse','Internal Warehouse')],
                                        string="Warehouse", default="internal_warehouse")
    enable_direct_pickup = fields.Boolean("Direct Pickup")
    enable_shipment_to_customer = fields.Boolean("Shipment To Customer")

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    warehouse_manager = fields.Boolean("WareHouse Manager")

class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    manager = fields.Many2one('hr.employee',"Manager", domain=[('warehouse_manager','=',True)])
