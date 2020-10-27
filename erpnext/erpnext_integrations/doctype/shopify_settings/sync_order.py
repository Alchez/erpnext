from shopify import Order, PaginatedIterator

import frappe
from erpnext.erpnext_integrations.connectors.shopify_connection import \
    sync_shopify_order
from erpnext.erpnext_integrations.doctype.shopify_log.shopify_log import \
    make_shopify_log


@frappe.whitelist()
def sync_orders_from_shopify():
	shopify_settings = frappe.get_single("Shopify Settings")
	if not shopify_settings.enable_shopify:
		return

	kwargs = dict(status="any")
	# if shopify_settings.last_sync_datetime:
	# 	kwargs['updated_at_min'] = shopify_settings.last_sync_datetime

	with shopify_settings.get_shopify_session(temp=True):
		try:
			shopify_orders = PaginatedIterator(Order.find(**kwargs))
		except Exception as e:
			make_shopify_log(status="Error", exception=e, rollback=True)
		else:
			bulk_sync_shopify_orders(shopify_orders)

			# TODO: figure out pickling error that occurs trying to enqueue
			# the bulk-sync sales order function

			# frappe.enqueue(method=bulk_sync_shopify_orders, queue='long',
			# 	is_async=True, **{"shopify_orders": shopify_orders})

	return True


def bulk_sync_shopify_orders(shopify_orders):
	for page in shopify_orders:
		for order in page:
			sync_shopify_order(order.to_dict())
