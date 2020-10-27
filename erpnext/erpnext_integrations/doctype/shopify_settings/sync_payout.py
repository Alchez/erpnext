from collections import defaultdict

from shopify import Order, PaginatedIterator, ShopifyResource

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from erpnext.erpnext_integrations.connectors.shopify_connection import sync_shopify_order
from erpnext.erpnext_integrations.doctype.shopify_log.shopify_log import make_shopify_log
from frappe.utils import now


class Payouts(ShopifyResource):
	# temporary class until https://github.com/Shopify/shopify_python_api/pull/431 is merged
	_prefix_source = "/shopify_payments/"


class Transactions(ShopifyResource):
	# temporary class until https://github.com/Shopify/shopify_python_api/pull/431 is merged
	_prefix_source = "/shopify_payments/balance/"


def get_payouts(shopify_settings):
	kwargs = dict(status="paid")
	# if shopify_settings.last_sync_datetime:
	# 	kwargs['date_min'] = shopify_settings.last_sync_datetime

	try:
		payouts = PaginatedIterator(Payouts.find(**kwargs))
	except Exception as e:
		make_shopify_log(status="Error", exception=e, rollback=True)
		return []
	else:
		return payouts


def get_shopify_invoice(order_id):
	return frappe.db.get_value("Sales Invoice", {"docstatus": 1, "shopify_order_id": order_id}, "name")


@frappe.whitelist()
def sync_payouts_from_shopify():
	"""
	Pull and sync payouts from Shopify Payments transactions with existing orders
	"""

	shopify_settings = frappe.get_single("Shopify Settings")
	if not shopify_settings.enable_shopify:
		return

	with shopify_settings.get_shopify_session(temp=True):
		payouts = get_payouts(shopify_settings)
		_sync_payout(payouts)

		# TODO: figure out pickling error that occurs trying to enqueue
		# the sync payout function

		# frappe.enqueue(method=_sync_payout, queue='long', is_async=True,
		# 	**{"payouts": payouts})

	shopify_settings.last_sync_datetime = now()
	shopify_settings.save()
	return True


def _sync_payout(payouts):
	payouts_by_order = defaultdict(list)
	for page in payouts:
		for payout in page:
			try:
				payout_transactions = Transactions.find(payout_id=payout.id)
			except Exception as e:
				make_shopify_log(status="Error", response_data=payout.to_dict(), exception=e)
				continue

			for transaction in payout_transactions:
				# ignore summary payout transactions
				if "payout" not in transaction.type:
					payouts_by_order[transaction.source_order_id].append(transaction)

	for order_id, order_transactions in payouts_by_order.items():
		# ignore summary transactions
		if not order_id:
			continue

		order = Order.find(order_id)
		invoice = get_sales_invoice(order)

		if not invoice:
			# no invoice to update charges
			continue

		order_fully_refunded = order.financial_status == "refunded"
		invoice_returned = frappe.db.get_value("Sales Invoice", invoice, "status") in ["Return",
			"Credit Note Issued"]

		if order_fully_refunded and not invoice_returned:
			return_invoice = make_sales_return(invoice)
			return_invoice.save()
			return_invoice.submit()
			continue

		for transaction in order_transactions:
			# check for charges / payouts
			if transaction.type == "charge":
				# TODO
				pass

			# check for adjustments
			if transaction.type == "adjustment":
				# TODO
				pass

			# check for refunds
			if transaction.type == "refund":
				# TODO
				pass


def get_sales_invoice(shopify_order):
	invoice = get_shopify_invoice(shopify_order.id)

	if not invoice:
		sync_shopify_order(shopify_order.to_dict())
		invoice = get_shopify_invoice(shopify_order.id)

	if not invoice:
		make_shopify_log(status="Missing Invoice", response_data=shopify_order.to_dict())

	return invoice
