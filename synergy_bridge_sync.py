"""
Synergy Monthly Bridge Auto-Sync
=================================
Listens for Smartsheet webhook events on the PMI Synergy Tracker.
When "Synergy Initiative" is checked on a row, automatically creates
Target / Baseline / Actuals rows in the Synergy Monthly Bridge sheet
for that row's Sub-Category (if not already present).
"""

import os
import logging
import requests
from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# Configuration — set these as environment variables in Render
# ---------------------------------------------------------------------------

API_TOKEN = os.environ.get("SMARTSHEET_API_TOKEN", "")
CALLBACK_URL = os.environ.get("WEBHOOK_CALLBACK_URL", "")
PORT = int(os.environ.get("PORT", 10000))   # Render uses 10000 by default

# Smartsheet Sheet IDs
SYNERGY_TRACKER_SHEET_ID = 8569817331093380
MONTHLY_BRIDGE_SHEET_ID = 7008991855988612

# Column IDs in the Monthly Bridge sheet (fixed — do not change)
BRIDGE_COL_SUBCATEGORY  = 7233659192250244
BRIDGE_COL_MEASURE_TYPE = 1604159658037124
BRIDGE_COL_CATEGORY     = 6107759285407620
BRIDGE_COL_WORKSTREAM   = 3855959471722372

# Smartsheet API base URL
API_BASE = "https://api.smartsheet.com/2.0"

# Measure types to create per sub-category
MEASURE_TYPES = ["Target", "Baseline", "Actuals"]

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
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }


def get_sheet(sheet_id):
    url = f"{API_BASE}/sheets/{sheet_id}"
    resp = requests.get(url, headers=ss_headers())
    resp.raise_for_status()
    return resp.json()


def add_rows_to_sheet(sheet_id, rows_payload):
    url = f"{API_BASE}/sheets/{sheet_id}/rows"
    resp = requests.post(url, headers=ss_headers(), json=rows_payload)
    resp.raise_for_status()
    return resp.json()


def get_column_map(columns):
    """Return {column_title: column_id} for a list of column objects."""
    return {col["title"]: col["id"] for col in columns}


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def get_flagged_subcategories():
    """
    Read the Synergy Tracker and return unique sub-categories where
    Synergy Initiative = True.

    Returns:
        dict: { sub_category: {"category": ..., "workstream": ...} }
    """
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
    col_map = get_column_map(sheet["columns"])

    # Column titles as they appear in the Synergy Tracker
    col_subcat   = col_map.get("Sub-Category")
    col_category = col_map.get("Category")
    col_wstream  = col_map.get("Workstream")
    col_syn_init = col_map.get("Synergy Initiative")

    if not col_syn_init:
        log.error("Could not find 'Synergy Initiative' column in Synergy Tracker")
        return {}

    flagged = {}
    for row in sheet.get("rows", []):
        cells = {c["columnId"]: c.get("value") for c in row["cells"]}

        if cells.get(col_syn_init) is True:
            sub_cat = cells.get(col_subcat, "")
            if sub_cat and sub_cat not in flagged:
                flagged[sub_cat] = {
                    "category":   cells.get(col_category, ""),
                    "workstream": cells.get(col_wstream, ""),
                }

    log.info(f"Synergy Tracker: {len(flagged)} unique flagged sub-categories")
    return flagged


def get_existing_subcategories():
    """
    Read the Monthly Bridge and return sub-categories that already
    have rows, so we don't create duplicates.

    Returns:
        set: Existing sub-category names
    """
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    col_map = get_column_map(sheet["columns"])
    col_subcat = col_map.get("Sub-Category")

    existing = set()
    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == col_subcat and cell.get("value"):
                existing.add(cell["value"])

    log.info(f"Monthly Bridge: {len(existing)} existing sub-categories")
    return existing


def create_bridge_rows(sub_category, category, workstream):
    """
    Create 3 rows (Target / Baseline / Actuals) in the Monthly Bridge
    for a new sub-category.
    """
    rows = []
    for measure in MEASURE_TYPES:
        rows.append({
            "toBottom": True,
            "cells": [
                {"columnId": BRIDGE_COL_SUBCATEGORY,  "value": sub_category},
                {"columnId": BRIDGE_COL_MEASURE_TYPE, "value": measure},
                {"columnId": BRIDGE_COL_CATEGORY,     "value": category},
                {"columnId": BRIDGE_COL_WORKSTREAM,   "value": workstream},
            ],
        })

    add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(f"Created Target/Baseline/Actuals rows for '{sub_category}'")


def sync_new_subcategories():
    """
    Main sync: find new sub-categories in Tracker not yet in Bridge
    and create their 3 rows.

    Returns:
        list: Names of newly synced sub-categories
    """
    flagged  = get_flagged_subcategories()
    existing = get_existing_subcategories()

    created = []
    for sub_cat, info in flagged.items():
        if sub_cat not in existing:
            log.info(f"New sub-category detected: '{sub_cat}' — creating rows")
            create_bridge_rows(sub_cat, info["category"], info["workstream"])
            created.append(sub_cat)

    if created:
        log.info(f"Sync complete — created rows for: {created}")
    else:
        log.info("Sync complete — no new sub-categories found")

    return created


# ---------------------------------------------------------------------------
# Webhook setup helpers
# ---------------------------------------------------------------------------

def register_webhook():
    """Register and enable the Smartsheet webhook."""
    # Check if it already exists
    existing = requests.get(f"{API_BASE}/webhooks", headers=ss_headers()).json()
    for wh in existing.get("data", []):
        if (wh.get("scopeObjectId") == SYNERGY_TRACKER_SHEET_ID
                and wh.get("callbackUrl") == CALLBACK_URL):
            log.info(f"Webhook already exists: ID {wh['id']}")
            return wh["id"]

    # Create new webhook
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

    # Enable it
    requests.put(
        f"{API_BASE}/webhooks/{webhook_id}",
        headers=ss_headers(),
        json={"enabled": True},
    ).raise_for_status()

    log.info(f"Webhook created and enabled: ID {webhook_id}")
    return webhook_id


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """
    Receives Smartsheet webhook callbacks.
    - Verification challenge: echoed back immediately
    - Change events: triggers sync
    """
    body = request.get_json(silent=True) or {}

    # Smartsheet verification handshake
    challenge = body.get("challenge")
    if challenge:
        log.info("Webhook verification challenge received — responding")
        return jsonify({"smartsheetHookResponse": challenge}), 200

    # Change event — run sync
    scope_id = body.get("scopeObjectId", 0)
    events   = body.get("events", [])
    log.info(f"Webhook event: scopeObjectId={scope_id}, events={len(events)}")

    if scope_id == SYNERGY_TRACKER_SHEET_ID:
        try:
            created = sync_new_subcategories()
            log.info(f"Webhook sync done — new rows: {created}")
        except Exception as e:
            log.error(f"Sync error: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["GET", "POST"])
def manual_sync():
    """Manual trigger — call this anytime to force a sync."""
    try:
        created = sync_new_subcategories()
        return jsonify({
            "status": "ok",
            "new_subcategories": created,
            "message": f"Created rows for {len(created)} sub-categories"
                       if created else "No new sub-categories to sync",
        }), 200
    except Exception as e:
        log.error(f"Manual sync error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/setup-webhook", methods=["GET", "POST"])
def setup_webhook():
    """One-time webhook registration — call after deployment."""
    if not CALLBACK_URL:
        return jsonify({
            "status": "error",
            "message": "WEBHOOK_CALLBACK_URL env var not set",
        }), 400
    try:
        webhook_id = register_webhook()
        return jsonify({"status": "ok", "webhook_id": webhook_id}), 200
    except Exception as e:
        log.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not API_TOKEN:
        log.error("SMARTSHEET_API_TOKEN is not set — exiting")
        exit(1)

    log.info(f"Starting Synergy Bridge Sync on port {PORT}")
    log.info(f"  Tracker sheet:  {SYNERGY_TRACKER_SHEET_ID}")
    log.info(f"  Bridge sheet:   {MONTHLY_BRIDGE_SHEET_ID}")
    log.info(f"  Callback URL:   {CALLBACK_URL or '(not set)'}")

    app.run(host="0.0.0.0", port=PORT)

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
    app.run(host="0.0.0.0", port=PORT)MEASURE_TYPES = ["Target", "Baseline", "Actuals"]

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
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }


def get_sheet(sheet_id):
    url = f"{API_BASE}/sheets/{sheet_id}"
    resp = requests.get(url, headers=ss_headers())
    resp.raise_for_status()
    return resp.json()


def add_rows_to_sheet(sheet_id, rows_payload):
    url = f"{API_BASE}/sheets/{sheet_id}/rows"
    resp = requests.post(url, headers=ss_headers(), json=rows_payload)
    resp.raise_for_status()
    return resp.json()


def get_column_map(columns):
    """Return {column_title: column_id} for a list of column objects."""
    return {col["title"]: col["id"] for col in columns}


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def get_flagged_subcategories():
    """
    Read the Synergy Tracker and return unique sub-categories where
    Synergy Initiative = True.

    Returns:
        dict: { sub_category: {"category": ..., "workstream": ...} }
    """
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
    col_map = get_column_map(sheet["columns"])

    # Column titles as they appear in the Synergy Tracker
    col_subcat   = col_map.get("Sub-Category")
    col_category = col_map.get("Category")
    col_wstream  = col_map.get("Workstream")
    col_syn_init = col_map.get("Synergy Initiative")

    if not col_syn_init:
        log.error("Could not find 'Synergy Initiative' column in Synergy Tracker")
        return {}

    flagged = {}
    for row in sheet.get("rows", []):
        cells = {c["columnId"]: c.get("value") for c in row["cells"]}

        if cells.get(col_syn_init) is True:
            sub_cat = cells.get(col_subcat, "")
            if sub_cat and sub_cat not in flagged:
                flagged[sub_cat] = {
                    "category":   cells.get(col_category, ""),
                    "workstream": cells.get(col_wstream, ""),
                }

    log.info(f"Synergy Tracker: {len(flagged)} unique flagged sub-categories")
    return flagged


def get_existing_subcategories():
    """
    Read the Monthly Bridge and return sub-categories that already
    have rows, so we don't create duplicates.

    Returns:
        set: Existing sub-category names
    """
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    col_map = get_column_map(sheet["columns"])
    col_subcat = col_map.get("Sub-Category")

    existing = set()
    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == col_subcat and cell.get("value"):
                existing.add(cell["value"])

    log.info(f"Monthly Bridge: {len(existing)} existing sub-categories")
    return existing


def create_bridge_rows(sub_category, category, workstream):
    """
    Create 3 rows (Target / Baseline / Actuals) in the Monthly Bridge
    for a new sub-category.
    """
    rows = []
    for measure in MEASURE_TYPES:
        rows.append({
            "toBottom": True,
            "cells": [
                {"columnId": BRIDGE_COL_SUBCATEGORY,  "value": sub_category},
                {"columnId": BRIDGE_COL_MEASURE_TYPE, "value": measure},
                {"columnId": BRIDGE_COL_CATEGORY,     "value": category},
                {"columnId": BRIDGE_COL_WORKSTREAM,   "value": workstream},
            ],
        })

    add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(f"Created Target/Baseline/Actuals rows for '{sub_category}'")


def sync_new_subcategories():
    """
    Main sync: find new sub-categories in Tracker not yet in Bridge
    and create their 3 rows.

    Returns:
        list: Names of newly synced sub-categories
    """
    flagged  = get_flagged_subcategories()
    existing = get_existing_subcategories()

    created = []
    for sub_cat, info in flagged.items():
        if sub_cat not in existing:
            log.info(f"New sub-category detected: '{sub_cat}' — creating rows")
            create_bridge_rows(sub_cat, info["category"], info["workstream"])
            created.append(sub_cat)

    if created:
        log.info(f"Sync complete — created rows for: {created}")
    else:
        log.info("Sync complete — no new sub-categories found")

    return created


# ---------------------------------------------------------------------------
# Webhook setup helpers
# ---------------------------------------------------------------------------

def register_webhook():
    """Register and enable the Smartsheet webhook."""
    # Check if it already exists
    existing = requests.get(f"{API_BASE}/webhooks", headers=ss_headers()).json()
    for wh in existing.get("data", []):
        if (wh.get("scopeObjectId") == SYNERGY_TRACKER_SHEET_ID
                and wh.get("callbackUrl") == CALLBACK_URL):
            log.info(f"Webhook already exists: ID {wh['id']}")
            return wh["id"]

    # Create new webhook
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

    # Enable it
    requests.put(
        f"{API_BASE}/webhooks/{webhook_id}",
        headers=ss_headers(),
        json={"enabled": True},
    ).raise_for_status()

    log.info(f"Webhook created and enabled: ID {webhook_id}")
    return webhook_id


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """
    Receives Smartsheet webhook callbacks.
    - Verification challenge: echoed back immediately
    - Change events: triggers sync
    """
    body = request.get_json(silent=True) or {}

    # Smartsheet verification handshake
    challenge = body.get("challenge")
    if challenge:
        log.info("Webhook verification challenge received — responding")
        return jsonify({"smartsheetHookResponse": challenge}), 200

    # Change event — run sync
    scope_id = body.get("scopeObjectId", 0)
    events   = body.get("events", [])
    log.info(f"Webhook event: scopeObjectId={scope_id}, events={len(events)}")

    if scope_id == SYNERGY_TRACKER_SHEET_ID:
        try:
            created = sync_new_subcategories()
            log.info(f"Webhook sync done — new rows: {created}")
        except Exception as e:
            log.error(f"Sync error: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["GET", "POST"])
def manual_sync():
    """Manual trigger — call this anytime to force a sync."""
    try:
        created = sync_new_subcategories()
        return jsonify({
            "status": "ok",
            "new_subcategories": created,
            "message": f"Created rows for {len(created)} sub-categories"
                       if created else "No new sub-categories to sync",
        }), 200
    except Exception as e:
        log.error(f"Manual sync error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/setup-webhook", methods=["GET", "POST"])
def setup_webhook():
    """One-time webhook registration — call after deployment."""
    if not CALLBACK_URL:
        return jsonify({
            "status": "error",
            "message": "WEBHOOK_CALLBACK_URL env var not set",
        }), 400
    try:
        webhook_id = register_webhook()
        return jsonify({"status": "ok", "webhook_id": webhook_id}), 200
    except Exception as e:
        log.error(f"Webhook setup error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not API_TOKEN:
        log.error("SMARTSHEET_API_TOKEN is not set — exiting")
        exit(1)

    log.info(f"Starting Synergy Bridge Sync on port {PORT}")
    log.info(f"  Tracker sheet:  {SYNERGY_TRACKER_SHEET_ID}")
    log.info(f"  Bridge sheet:   {MONTHLY_BRIDGE_SHEET_ID}")
    log.info(f"  Callback URL:   {CALLBACK_URL or '(not set)'}")

    app.run(host="0.0.0.0", port=PORT)
