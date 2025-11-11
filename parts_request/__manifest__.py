# -*- coding: utf-8 -*-
{
    'name': "Parts Request",

    'summary': "Streamlined parts request and approval flow with FOC/chargeable handling, contract integration, and payment tracking for Field Service.",

    'description': """
    Manage and track asset part requests and approvals in Field Service. 
    Supports FOC and chargeable parts with supervisor and customer approval flows. 
    Includes integration with warehouse, contracts, and payment handling.
    """,

    'author': "Effezient",
    'license': 'LGPL-3',
    'category': 'Parts Approver',
    'sequence': 170,
    'version': '1.0',

    'depends': ['base','inventory_custom_tracking_installation_delivery','industry_fsm','customer_app'],

    'data': [
        'security/ir.model.access.csv',
        'security/part_approval_security.xml',
        'views/contract_type.xml',
        'views/portal_template_views.xml',
        'views/part_model.xml',
        'views/part_approval_notification.xml',
        'views/res_company.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
}
