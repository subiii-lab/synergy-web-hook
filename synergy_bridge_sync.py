"""
Synergy Monthly Bridge Auto-Sync
================================
Smartsheet Webhook listener that automatically creates Target/Forecast/Actual
rows in the Synergy Monthly Bridge sheet whenever "Synergy Initiative" is
checked in the PMI Synergy Tracker.

Usage:
    python synergy_bridge_sync.py

Environment variables required:
    SMARTSHEET_API_TOKEN  - Your Smartsheet API token
    WEBHOOK_CALLBACK_URL  - Public URL where this server is reachable
                            (e.g. https://your-domain.com/webhook or ngrok URL)

Optional:
    PORT                  - Port to run on (default: 5000)
"""

import os
import json
import hmac
import hashlib
import logging
import requests
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("SMARTSHEET_API_TOKEN", "")
CALLBACK_URL = os.environ.get("WEBHOOK_CALLBACK_URL", "")
PORT = int(os.environ.get("PORT", 5000))

# Smartsheet IDs
SYNERGY_TRACKER_SHEET_ID = 8569817331093380
MONTHLY_BRIDGE_SHEET_ID = 7008991855988612

# Column IDs in the Monthly Bridge sheet
BRIDGE_COL_SUBCATEGORY = 7233659192250244
BRIDGE_COL_MEASURE_TYPE = 1604159658037124
BRIDGE_COL_CATEGORY = 6107759285407620
BRIDGE_COL_WORKSTREAM = 3855959471722372

# Smartsheet API base
API_BASE = "https://api.smartsheet.com/2.0"

# Measure types to create for each sub-category
MEASURE_TYPES = ["Target", "Forecast", "Actual"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Smartsheet API helpers
# ---------------------------------------------------------------------------

def ss_headers():
    """Return standard Smartsheet API headers."""
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }


def get_sheet(sheet_id):
    """Fetch full sheet data from Smartsheet."""
    url = f"{API_BASE}/sheets/{sheet_id}"
    resp = requests.get(url, headers=ss_headers())
    resp.raise_for_status()
    return resp.json()


def add_rows_to_sheet(sheet_id, rows_payload):
    """Add rows to a Smartsheet sheet."""
    url = f"{API_BASE}/sheets/{sheet_id}/rows"
    resp = requests.post(url, headers=ss_headers(), json=rows_payload)
    resp.raise_for_status()
    return resp.json()


def get_column_id_by_title(columns, title):
    """Find a column ID by its title."""
    for col in columns:
        if col["title"] == title:
            return col["id"]
    return None


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def get_flagged_subcategories():
    """
    Read the Synergy Tracker and return a dict of unique sub-categories
    that have "Synergy Initiative" checked.

    Returns:
        dict: {sub_category: {"category": ..., "workstream": ...}}
    """
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
    columns = sheet["columns"]

    col_subcategory = get_column_id_by_title(columns, "Sub-Category")
    col_category = get_column_id_by_title(columns, "Category")
    col_workstream = get_column_id_by_title(columns, "Workstream")
    col_synergy_init = get_column_id_by_title(columns, "Synergy Initiative")

    flagged = {}
    for row in sheet.get("rows", []):
        cells = {c["columnId"]: c.get("value") for c in row["cells"]}

        # Check if Synergy Initiative is True
        if cells.get(col_synergy_init) is True:
            sub_cat = cells.get(col_subcategory, "")
            if sub_cat and sub_cat not in flagged:
                flagged[sub_cat] = {
                    "category": cells.get(col_category, ""),
                    "workstream": cells.get(col_workstream, ""),
                }

    log.info(f"Found {len(flagged)} unique flagged sub-categories in Synergy Tracker")
    return flagged


def get_existing_subcategories():
    """
    Read the Monthly Bridge and return a set of sub-categories that
    already have rows.

    Returns:
        set: Set of existing sub-category names
    """
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    columns = sheet["columns"]
    col_subcategory = get_column_id_by_title(columns, "Sub-Category")

    existing = set()
    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == col_subcategory and cell.get("value"):
                existing.add(cell["value"])

    log.info(f"Found {len(existing)} existing sub-categories in Monthly Bridge")
    return existing


def create_bridge_rows(sub_category, category, workstream):
    """
    Create 3 rows (Target, Forecast, Actual) in the Monthly Bridge
    for the given sub-category.
    """
    rows = []
    for measure in MEASURE_TYPES:
        rows.append({
            "toBottom": True,
            "cells": [
                {"columnId": BRIDGE_COL_SUBCATEGORY, "value": sub_category},
                {"columnId": BRIDGE_COL_MEASURE_TYPE, "value": measure},
                {"columnId": BRIDGE_COL_CATEGORY, "value": category},
                {"columnId": BRIDGE_COL_WORKSTREAM, "value": workstream},
            ],
        })

    result = add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(
        f"Created 3 rows for '{sub_category}' "
        f"(Target/Forecast/Actual) in Monthly Bridge"
    )
    return result


def sync_new_subcategories():
    """
    Main sync logic: compare Tracker vs Bridge, create rows for any
    new sub-categories.

    Returns:
        list: Names of newly synced sub-categories
    """
    flagged = get_flagged_subcategories()
    existing = get_existing_subcategories()

    new_subcats = []
    for sub_cat, info in flagged.items():
        if sub_cat not in existing:
            log.info(f"New sub-category found: '{sub_cat}' - creating rows...")
            create_bridge_rows(sub_cat, info["category"], info["workstream"])
            new_subcats.append(sub_cat)

    if not new_subcats:
        log.info("No new sub-categories to sync.")
    else:
        log.info(f"Synced {len(new_subcats)} new sub-categories: {new_subcats}")

    return new_subcats


# ---------------------------------------------------------------------------
# Webhook management
# ---------------------------------------------------------------------------

def create_webhook():
    """Register a webhook with Smartsheet for the Synergy Tracker sheet."""
    url = f"{API_BASE}/webhooks"
    payload = {
        "name": "Synergy Initiative Auto-Sync",
        "callbackUrl": CALLBACK_URL,
        "scope": "sheet",
        "scopeObjectId": SYNERGY_TRACKER_SHEET_ID,
        "events": ["*.*"],
        "version": 1,
    }
    resp = requests.post(url, headers=ss_headers(), json=payload)
    resp.raise_for_status()
    data = resp.json()
    webhook_id = data["result"]["id"]
    log.info(f"Webhook created with ID: {webhook_id}")

    # Enable the webhook
    enable_url = f"{API_BASE}/webhooks/{webhook_id}"
    enable_payload = {"enabled": True}
    resp = requests.put(enable_url, headers=ss_headers(), json=enable_payload)
    resp.raise_for_status()
    log.info(f"Webhook {webhook_id} enabled successfully")

    return webhook_id


def list_webhooks():
    """List all existing webhooks."""
    url = f"{API_BASE}/webhooks"
    resp = requests.get(url, headers=ss_headers())
    resp.raise_for_status()
    return resp.json().get("data", [])


# ---------------------------------------------------------------------------
# Flask app (webhook receiver)
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """
    Handle incoming Smartsheet webhook callbacks.

    Smartsheet sends two types of requests:
    1. Verification (initial handshake) - respond with the challenge
    2. Event notifications - process the changes
    """
    body = request.get_json(silent=True) or {}

    # ---- Verification challenge ----
    # When you first enable a webhook, Smartsheet sends a verification
    # request. You must echo back the challenge value.
    challenge = body.get("challenge")
    if challenge:
        log.info(f"Received verification challenge: {challenge}")
        return jsonify({"smartsheetHookResponse": challenge}), 200

    # ---- Event notification ----
    # Smartsheet notifies us that something changed on the sheet.
    # We don't parse individual cell changes — instead we just re-run
    # the full sync to check for any new sub-categories.
    scope = body.get("scope", "")
    scope_id = body.get("scopeObjectId", 0)
    events = body.get("events", [])

    log.info(
        f"Webhook event received: scope={scope}, "
        f"scopeObjectId={scope_id}, events={len(events)}"
    )

    # Only process if it's our Synergy Tracker sheet
    if scope_id == SYNERGY_TRACKER_SHEET_ID:
        try:
            new_subcats = sync_new_subcategories()
            if new_subcats:
                log.info(f"Auto-created rows for: {new_subcats}")
        except Exception as e:
            log.error(f"Error during sync: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["POST", "GET"])
def manual_sync():
    """
    Manual trigger endpoint. Hit this to force a sync without
    waiting for a webhook event.

    GET or POST http://localhost:5000/sync
    """
    try:
        new_subcats = sync_new_subcategories()
        return jsonify({
            "status": "ok",
            "new_subcategories": new_subcats,
            "message": f"Synced {len(new_subcats)} new sub-categories"
            if new_subcats
            else "No new sub-categories to sync",
        }), 200
    except Exception as e:
        log.error(f"Manual sync error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "message": "Synergy Bridge Sync is running"}), 200


@app.route("/setup-webhook", methods=["POST"])
def setup_webhook_endpoint():
    """
    One-time setup: creates and enables the Smartsheet webhook.
    POST http://localhost:5000/setup-webhook
    """
    try:
        # Check for existing webhooks first
        existing = list_webhooks()
        for wh in existing:
            if (
                wh.get("scopeObjectId") == SYNERGY_TRACKER_SHEET_ID
                and wh.get("callbackUrl") == CALLBACK_URL
            ):
                return jsonify({
                    "status": "exists",
                    "webhook_id": wh["id"],
                    "message": "Webhook already exists for this sheet + URL",
                }), 200

        webhook_id = create_webhook()
        return jsonify({
            "status": "created",
            "webhook_id": webhook_id,
            "message": "Webhook created and enabled",
        }), 200
    except Exception as e:
        log.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not API_TOKEN:
        log.error(
            "SMARTSHEET_API_TOKEN environment variable is not set. "
            "Get your token from: Account > Personal Settings > API Access > "
            "Generate New Access Token"
        )
        exit(1)

    if not CALLBACK_URL:
        log.warning(
            "WEBHOOK_CALLBACK_URL is not set. The /sync endpoint will still "
            "work for manual triggers, but the webhook won't be registered. "
            "Set it to your public URL (e.g. https://your-ngrok-url/webhook)"
        )

    log.info(f"Starting Synergy Bridge Sync server on port {PORT}")
    log.info(f"  Synergy Tracker Sheet: {SYNERGY_TRACKER_SHEET_ID}")
    log.info(f"  Monthly Bridge Sheet:  {MONTHLY_BRIDGE_SHEET_ID}")
    log.info(f"  Callback URL:          {CALLBACK_URL or '(not set)'}")
    log.info(f"  Endpoints:")
    log.info(f"    POST /webhook         - Smartsheet webhook receiver")
    log.info(f"    GET  /sync            - Manual sync trigger")
    log.info(f"    POST /setup-webhook   - Register the webhook with Smartsheet")
    log.info(f"    GET  /health          - Health check")

    app.run(host="0.0.0.0", port=PORT, debug=False)
