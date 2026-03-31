"""
Synergy Monthly Bridge Auto-Sync
=================================
Listens for Smartsheet webhook events on the PMI Synergy Tracker.
When "Synergy Initiative" is checked on a row, automatically creates
Target / Forecast / Actual rows in the Synergy Monthly Bridge sheet
for that Task ID (if not already present).
"""

import os
import logging
import requests
from flask import Flask, request, jsonify

API_TOKEN = os.environ.get("SMARTSHEET_API_TOKEN", "")
CALLBACK_URL = os.environ.get("WEBHOOK_CALLBACK_URL", "")
PORT = int(os.environ.get("PORT", 10000))

SYNERGY_TRACKER_SHEET_ID = 8569817331093380
MONTHLY_BRIDGE_SHEET_ID  = 7008991855988612

# --- Synergy Tracker column IDs ---
TRACKER_COL_TASK_ID     = 7489979694747524
TRACKER_COL_TASK_NAME   = 7932855440412548
TRACKER_COL_SUBCATEGORY = 6364079787904900   # MULTI_PICKLIST — read via objectValue
TRACKER_COL_CATEGORY    = 1860480160534404
TRACKER_COL_WORKSTREAM  = 3066897206906756
TRACKER_COL_SYN_INIT    = 3306555283115908   # CHECKBOX

# --- Monthly Bridge column IDs ---
BRIDGE_COL_TASK_ID      = 8908231146770308
BRIDGE_COL_TASK_NAME    = 621081481482116
BRIDGE_COL_SUBCATEGORY  = 7233659192250244
BRIDGE_COL_WORKSTREAM   = 3855959471722372
BRIDGE_COL_CATEGORY     = 6107759285407620
BRIDGE_COL_MEASURE_TYPE = 1604159658037124

API_BASE = "https://api.smartsheet.com/2.0"
MEASURE_TYPES = ["Target", "Forecast", "Actual"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def ss_headers():
    return {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}


def get_sheet(sheet_id, include_object_value=False):
    params = "?include=objectValue" if include_object_value else ""
    resp = requests.get(f"{API_BASE}/sheets/{sheet_id}{params}", headers=ss_headers())
    resp.raise_for_status()
    return resp.json()


def add_rows_to_sheet(sheet_id, rows_payload):
    resp = requests.post(f"{API_BASE}/sheets/{sheet_id}/rows", headers=ss_headers(), json=rows_payload)
    resp.raise_for_status()
    return resp.json()


def extract_cell_value(cell):
    """
    Extract value from a cell, handling MULTI_PICKLIST objectValue.
    MULTI_PICKLIST columns return selected values in objectValue.values[].
    Multiple selections are joined with ', '.
    """
    value = cell.get("value")
    if value is not None:
        return value
    obj = cell.get("objectValue")
    if obj and isinstance(obj, dict):
        values = obj.get("values", [])
        if values:
            return ", ".join(str(v) for v in values)
    return None


def get_flagged_initiatives():
    """
    Read the Synergy Tracker and return a dict of:
      { task_id: { task_name, sub_category, category, workstream } }
    for every row where Synergy Initiative checkbox is True.
    """
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID, include_object_value=True)
    initiatives = {}

    for row in sheet.get("rows", []):
        cells_by_id = {c["columnId"]: c for c in row["cells"]}

        syn_init_cell = cells_by_id.get(TRACKER_COL_SYN_INIT, {})
        if syn_init_cell.get("value") is not True:
            continue

        task_id = extract_cell_value(cells_by_id.get(TRACKER_COL_TASK_ID, {}))
        if not task_id:
            log.warning(f"Synergy Initiative row {row['id']} has no Task ID — skipping")
            continue

        task_id = str(task_id).strip()
        initiatives[task_id] = {
            "task_name":    extract_cell_value(cells_by_id.get(TRACKER_COL_TASK_NAME, {})) or "",
            "sub_category": extract_cell_value(cells_by_id.get(TRACKER_COL_SUBCATEGORY, {})) or "",
            "category":     extract_cell_value(cells_by_id.get(TRACKER_COL_CATEGORY, {})) or "",
            "workstream":   extract_cell_value(cells_by_id.get(TRACKER_COL_WORKSTREAM, {})) or "",
        }

    log.info(f"Synergy Tracker: {len(initiatives)} flagged initiatives")
    return initiatives


def get_existing_task_ids():
    """Read the Monthly Bridge and return the set of Task IDs already present."""
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    existing = set()
    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == BRIDGE_COL_TASK_ID and cell.get("value"):
                existing.add(str(cell["value"]).strip())
    log.info(f"Monthly Bridge: {len(existing)} existing Task IDs")
    return existing


def create_bridge_rows(task_id, info):
    """Create Target / Forecast / Actual rows in the Monthly Bridge for a Task ID."""
    rows = []
    for measure in MEASURE_TYPES:
        rows.append({
            "toBottom": True,
            "cells": [
                {"columnId": BRIDGE_COL_TASK_ID,      "value": task_id},
                {"columnId": BRIDGE_COL_TASK_NAME,    "value": info["task_name"]},
                {"columnId": BRIDGE_COL_SUBCATEGORY,  "value": info["sub_category"]},
                {"columnId": BRIDGE_COL_CATEGORY,     "value": info["category"]},
                {"columnId": BRIDGE_COL_WORKSTREAM,   "value": info["workstream"]},
                {"columnId": BRIDGE_COL_MEASURE_TYPE, "value": measure},
            ],
        })
    add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(f"Created Target/Forecast/Actual rows for Task ID '{task_id}' ({info['task_name']})")


def sync_new_initiatives():
    """Find new Task IDs in Tracker not yet in Bridge and create their 3 rows."""
    flagged  = get_flagged_initiatives()
    existing = get_existing_task_ids()

    created = []
    for task_id, info in flagged.items():
        if task_id not in existing:
            log.info(f"New initiative: Task ID '{task_id}' — creating rows")
            create_bridge_rows(task_id, info)
            created.append(task_id)

    if created:
        log.info(f"Sync complete — created rows for Task IDs: {created}")
    else:
        log.info("Sync complete — no new initiatives found")
    return created


app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    body = request.get_json(silent=True) or {}
    challenge = body.get("challenge")
    if challenge:
        log.info("Webhook verification challenge — responding")
        return jsonify({"smartsheetHookResponse": challenge}), 200

    scope_id = body.get("scopeObjectId", 0)
    events   = body.get("events", [])
    log.info(f"Webhook event received: scopeObjectId={scope_id}, events={len(events)}")

    if scope_id == SYNERGY_TRACKER_SHEET_ID:
        try:
            created = sync_new_initiatives()
            log.info(f"Webhook sync done — new Task IDs: {created}")
        except Exception as e:
            log.error(f"Sync error: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["GET", "POST"])
def manual_sync():
    try:
        created = sync_new_initiatives()
        return jsonify({
            "status": "ok",
            "new_task_ids": created,
            "message": (
                f"Created rows for {len(created)} Task IDs: {created}"
                if created else "No new initiatives to sync"
            ),
        }), 200
    except Exception as e:
        log.error(f"Manual sync error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/setup-webhook", methods=["GET", "POST"])
def setup_webhook():
    if not CALLBACK_URL:
        return jsonify({"status": "error", "message": "WEBHOOK_CALLBACK_URL env var not set"}), 400
    try:
        existing = requests.get(f"{API_BASE}/webhooks", headers=ss_headers()).json()
        for wh in existing.get("data", []):
            if (wh.get("scopeObjectId") == SYNERGY_TRACKER_SHEET_ID
                    and wh.get("callbackUrl") == CALLBACK_URL):
                log.info(f"Webhook already exists: ID {wh['id']}")
                return jsonify({"status": "exists", "webhook_id": wh["id"]}), 200

        payload = {
            "name": "Synergy Initiative Auto-Sync",
            "callbackUrl": CALLBACK_URL,
            "scope": "sheet",
            "scopeObjectId": SYNERGY_TRACKER_SHEET_ID,
            "events": ["*.*"],
            "version": 1,
        }
        resp = requests.post(f"{API_BASE}/webhooks", headers=ss_headers(), json=payload)
        resp.raise_for_status()
        webhook_id = resp.json()["result"]["id"]

        requests.put(
            f"{API_BASE}/webhooks/{webhook_id}",
            headers=ss_headers(),
            json={"enabled": True},
        ).raise_for_status()

        log.info(f"Webhook created and enabled: ID {webhook_id}")
        return jsonify({"status": "created", "webhook_id": webhook_id}), 200

    except Exception as e:
        log.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    if not API_TOKEN:
        log.error("SMARTSHEET_API_TOKEN is not set — exiting")
        exit(1)
    log.info(f"Starting Synergy Bridge Sync on port {PORT}")
    log.info(f"  Tracker sheet : {SYNERGY_TRACKER_SHEET_ID}")
    log.info(f"  Bridge sheet  : {MONTHLY_BRIDGE_SHEET_ID}")
    log.info(f"  Callback URL  : {CALLBACK_URL or '(not set)'}")
    app.run(host="0.0.0.0", port=PORT)
