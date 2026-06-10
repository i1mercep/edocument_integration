# Copyright (c) 2026, Prilk Consulting BV and contributors
# For license information, please see license.txt

"""
Peppyrus PEPPOL API Client

This module provides a client for interacting with the Peppyrus PEPPOL API.
"""

import json
from typing import Any

import frappe
import requests
from frappe import _

BASE_URL = "https://api.peppyrus.be/v1"


class PeppyrusAPIClient:
	"""Client for interacting with the Peppyrus PEPPOL API using API key header auth."""

	def __init__(self, api_key: str, base_url: str = BASE_URL):
		self.api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.session = requests.Session()

		# Use API key header like B2B Router
		self.session.headers.update(
			{
				"X-PEPPYRUS-API-Key": api_key,
				"Accept": "application/json",
				"User-Agent": "Frappe-EDocument/1.0",
			}
		)

	def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
		url = f"{self.base_url}{endpoint}"

		try:
			response = self.session.request(method, url, **kwargs)
			return response
		except requests.exceptions.RequestException as exc:
			error_msg = f"Peppyrus API request failed: {exc!s}"
			frappe.log_error(error_msg, "Peppyrus API Error")
			raise

	# DOCUMENT TRANSMISSION METHODS

	def send_document(self, company_id: str, xml_content: str, document_type: str = "xml") -> dict[str, Any]:
		"""Send a PEPPOL document via Peppyrus.

		The payload mirrors the Recommand payload to keep the integration consistent.
		"""
		endpoint = f"/peppol/{company_id}/sendDocument"

		if isinstance(xml_content, bytes):
			xml_content_str = xml_content.decode("utf-8")
		else:
			xml_content_str = xml_content

		# Extract recipient from UBL XML using same namespace helpers
		from lxml import etree as ET

		try:
			from edocument.edocument.profiles.peppol import UBL_NAMESPACES

			root = ET.fromstring(
				xml_content_str.encode("utf-8") if isinstance(xml_content_str, str) else xml_content_str
			)

			customer_endpoint = root.find(
				".//cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", UBL_NAMESPACES
			)

			if customer_endpoint is not None:
				recipient_id = customer_endpoint.text
				recipient_scheme = customer_endpoint.get("schemeID", "0088")
				recipient = f"{recipient_scheme}:{recipient_id}"
			else:
				raise Exception(
					"Recipient EndpointID not found in XML. Please configure electronic address for customer."
				)
		except ET.ParseError as e:
			raise Exception(f"Failed to parse XML to extract recipient: {e!s}")
		except Exception as e:
			raise Exception(f"Failed to extract recipient from XML: {e!s}")

		payload = {
			"recipient": recipient,
			"documentType": document_type,
			"document": xml_content_str,
		}

		try:
			response = self._make_request("POST", endpoint, json=payload)

			if not response.ok:
				try:
					error_data = response.json()
					error_text = json.dumps(error_data)
				except json.JSONDecodeError:
					error_text = response.text

				error_msg = f"Peppyrus API error ({response.status_code}): {error_text}"
				frappe.log_error(f"{error_msg}\nRequest Payload: {json.dumps(payload)}", "Peppyrus API Error")
				raise Exception(error_msg)

			result = response.json()
			document_id = result.get("id", "unknown")

			return {"status": "success", "document_id": document_id, "response": result}

		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus document transmission failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Transmission Error")
			raise Exception(error_msg)

	def get_documents(self, team_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
		endpoint = f"/peppol/{team_id}/documents"
		params = {"limit": limit, "offset": offset}
		try:
			response = self._make_request("GET", endpoint, params=params)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus get documents failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Get Documents Error")
			raise Exception(error_msg)

	def get_inbox(self, team_id: str, company_id: str | None = None) -> dict[str, Any]:
		endpoint = f"/peppol/{team_id}/inbox"
		params = {}
		if company_id:
			params["companyId"] = company_id

		try:
			response = self._make_request("GET", endpoint, params=params)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus get inbox failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Get Inbox Error")
			raise Exception(error_msg)

	def get_document_status(self, team_id: str, document_id: str) -> dict[str, Any]:
		endpoint = f"/peppol/{team_id}/documents/{document_id}"
		try:
			response = self._make_request("GET", endpoint)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus get document status failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Document Status Error")
			raise Exception(error_msg)


# HELPER FUNCTIONS


def get_peppyrus_client(integration_settings: dict[str, Any]) -> PeppyrusAPIClient:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise Exception("Peppyrus integration settings must be provided as a dict")

	api_key = integration_settings.get("api_key")
	base_url = integration_settings.get("base_url", "https://api.peppyrus.be/v1")

	if not api_key:
		raise Exception("Peppyrus API key not configured")

	return PeppyrusAPIClient(api_key, base_url)


def transmit_invoice(xml_content: str, invoice_doc=None, integration_settings=None) -> dict[str, Any]:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise Exception("Peppyrus integration settings are required for transmission and must be a dict")

	try:
		client = get_peppyrus_client(integration_settings)

		company_id = integration_settings.get("company_id")
		if not company_id:
			raise Exception("Company ID not configured in integration settings (company_id field)")

		transmission_result = client.send_document(
			company_id=company_id, xml_content=xml_content, document_type="xml"
		)

		return {
			"status": "success",
			"document_id": transmission_result.get("document_id"),
			"response": transmission_result,
		}

	except Exception as e:
		error_msg = f"Peppyrus transmission failed: {e!s}"
		frappe.log_error(error_msg, "Peppyrus Transmission Error")
		raise Exception(error_msg)


def validate_peppyrus_connection(
	api_key: str, base_url: str = "https://api.peppyrus.be/v1"
) -> dict[str, Any]:
	try:
		client = PeppyrusAPIClient(api_key, base_url)
		response = client._make_request("GET", "/health")
		if response.status_code == 200:
			return {"status": "success", "message": "Peppyrus API connection successful"}
		else:
			return {"status": "error", "message": f"Peppyrus API connection failed: {response.status_code}"}
	except Exception as e:
		return {"status": "error", "message": f"Peppyrus API connection failed: {e!s}"}


def poll_inbox(
	integration_settings: dict[str, Any] | None = None, company_id: str | None = None
) -> dict[str, Any]:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise Exception("Peppyrus integration settings are required for inbox polling and must be a dict")

	client = get_peppyrus_client(integration_settings)

	team_id = integration_settings.get("account_id")
	if not team_id:
		raise Exception("Account ID (team ID) not found in integration settings")

	company_id = company_id or integration_settings.get("company_id")
	if not company_id:
		raise Exception("Company ID not found in integration settings")

	inbox_result = client.get_inbox(team_id, company_id=company_id)
	documents = inbox_result.get("documents", []) or inbox_result.get("data", []) or []

	if not documents:
		return {"status": "success", "message": "No new invoices found", "invoices": []}

	invoices = []
	for doc in documents:
		try:
			document_id = doc.get("id")
			doc_details = client.get_document_status(team_id, document_id)

			document_obj = doc_details.get("document", {})
			if not document_obj:
				frappe.log_error(
					f"No 'document' key found in response for {document_id}. Response keys: {list(doc_details.keys())}",
					"Peppyrus Inbox Polling Error",
				)
				continue

			xml_content = document_obj.get("xml")
			if not xml_content:
				frappe.log_error(
					f"No 'xml' key found in document object for {document_id}. Document keys: {list(document_obj.keys())}",
					"Peppyrus Inbox Polling Error",
				)
				continue

			xml_bytes = xml_content.encode("utf-8")
			invoices.append({"xml_bytes": xml_bytes, "document_id": document_id, "metadata": doc})
		except Exception as e:
			frappe.log_error(
				f"Failed to fetch document {doc.get('id')}: {e!s}\nTraceback: {frappe.get_traceback()}",
				"Peppyrus Inbox Polling Error",
			)

	return {"status": "success", "message": f"Found {len(invoices)} invoice(s)", "invoices": invoices}
