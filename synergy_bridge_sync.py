import os
import logging
import requests
from flask import Flask, request, jsonify

API_TOKEN = os.environ.get("SMARTSHEET_API_TOKEN", "")
CALLBACK_URL = os.environ.get("WEBHOOK_CALLBACK_URL", "")
PORT = int(os.environ.get("PORT", 5000))

SYNERGY_TRACKER_SHEET_ID = 8569817331093380
MONTHLY_BRIDGE_SHEET_ID = 7008991855988612

API_BASE = "https://api.smartsheet.com/2.0"

MEASURE_TYPES = ["Target", "Baseline", "Actuals"]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


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
    return {col["title"]: col["id"] for col in columns}


# --- CORE LOGIC ---

def get_tracker_rows():
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
    col_map = get_column_map(sheet["columns"])
    required_cols = ["Task ID", "Task Name", "Category", "SubCategory", "Workstream"]

    rows_data = []
    for row in sheet.get("rows", []):
        cell_map = {c["columnId"]: c.get("value") for c in row["cells"]}
        record = {}
        for col in required_cols:
            col_id = col_map.get(col)
            record[col] = cell_map.get(col_id)
        if record.get("Task ID"):
            rows_data.append(record)
    return rows_data


def get_existing_task_ids():
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    col_map = get_column_map(sheet["columns"])
    task_id_col = col_map.get("Task ID")

    existing = set()
    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == task_id_col and cell.get("value"):
                existing.add(cell["value"])
    return existing


def create_bridge_rows(record, bridge_col_map):
    rows = []
    for measure in MEASURE_TYPES:
        cells = []
        if "Task ID" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["Task ID"], "value": record["Task ID"]})
        if "Task Name" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["Task Name"], "value": record["Task Name"]})
        if "Category" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["Category"], "value": record["Category"]})
        if "SubCategory" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["SubCategory"], "value": record["SubCategory"]})
        if "Workstream" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["Workstream"], "value": record["Workstream"]})
        if "Measure Type" in bridge_col_map:
            cells.append({"columnId": bridge_col_map["Measure Type"], "value": measure})

        rows.append({"toBottom": True, "cells": cells})

    add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(f"Created 3 rows for Task ID {record['Task ID']}")


def sync_rows():
    tracker_rows = get_tracker_rows()
    existing_ids = get_existing_task_ids()

    bridge_sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    bridge_col_map = get_column_map(bridge_sheet["columns"])

    created = []
    for record in tracker_rows:
        task_id = record.get("Task ID")
        if task_id not in existing_ids:
            create_bridge_rows(record, bridge_col_map)
            created.append(task_id)

    log.info(f"Created rows for {len(created)} new tasks")
    return created


# --- FLASK ---

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    body = request.get_json(silent=True) or {}
    challenge = body.get("challenge")
    if challenge:
        return jsonify({"smartsheetHookResponse": challenge}), 200

    try:
        created = sync_rows()
        log.info(f"Webhook sync created: {created}")
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["GET"])
def manual_sync():
    try:
        created = sync_rows()
        return jsonify({"created": created}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/debug", methods=["GET"])
def debug_info():
    """Shows exactly what the code sees in both sheets — use this to diagnose issues."""
    try:
        # Check tracker sheet
        tracker_sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
        tracker_col_map = get_column_map(tracker_sheet["columns"])
        tracker_columns = list(tracker_col_map.keys())
        tracker_rows = get_tracker_rows()

        # Check bridge sheet
        bridge_sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
        bridge_col_map = get_column_map(bridge_sheet["columns"])
        bridge_columns = list(bridge_col_map.keys())
        existing_ids = get_existing_task_ids()

        # Find new tasks
        new_tasks = [r for r in tracker_rows if r.get("Task ID") not in existing_ids]

        return jsonify({
            "tracker_sheet": {
                "name": tracker_sheet.get("name"),
                "column_names": tracker_columns,
                "total_rows": len(tracker_sheet.get("rows", [])),
                "rows_with_task_id": len(tracker_rows),
                "sample_rows": tracker_rows[:3],
            },
            "bridge_sheet": {
                "name": bridge_sheet.get("name"),
                "column_names": bridge_columns,
                "total_rows": len(bridge_sheet.get("rows", [])),
                "existing_task_ids": list(existing_ids)[:10],
            },
            "sync_status": {
                "new_tasks_to_create": len(new_tasks),
                "new_task_ids": [r.get("Task ID") for r in new_tasks[:5]],
            },
            "api_token_set": bool(API_TOKEN),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
    url = f"{API_BASE}/sheets/{sheet_id}/rows"
    resp = requests.post(url, headers=ss_headers(), json=rows_payload)
    resp.raise_for_status()
    return resp.json()


def get_column_map(columns):
    return {col["title"]: col["id"] for col in columns}


# --- CORE LOGIC ---

def get_tracker_rows():
    sheet = get_sheet(SYNERGY_TRACKER_SHEET_ID)
    col_map = get_column_map(sheet["columns"])

    required_cols = ["Task ID", "Task Name", "Category", "Sub-Category", "Workstream"]

    rows_data = []
    for row in sheet.get("rows", []):
        cell_map = {c["columnId"]: c.get("value") for c in row["cells"]}

        record = {}
        for col in required_cols:
            col_id = col_map.get(col)
            record[col] = cell_map.get(col_id)

        if record.get("Task ID"):
            rows_data.append(record)

    return rows_data


def get_existing_task_ids():
    sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    col_map = get_column_map(sheet["columns"])

    task_id_col = col_map.get("Task ID")
    existing = set()

    for row in sheet.get("rows", []):
        for cell in row["cells"]:
            if cell["columnId"] == task_id_col and cell.get("value"):
                existing.add(cell["value"])

    return existing


def create_bridge_rows(record, bridge_col_map):
    rows = []

    for measure in MEASURE_TYPES:
        rows.append({
            "toBottom": True,
            "cells": [
                {"columnId": bridge_col_map["Task ID"], "value": record["Task ID"]},
                {"columnId": bridge_col_map["Task Name"], "value": record["Task Name"]},
                {"columnId": bridge_col_map["Category"], "value": record["Category"]},
                {"columnId": bridge_col_map["Sub-Category"], "value": record["Sub-Category"]},
                {"columnId": bridge_col_map["Workstream"], "value": record["Workstream"]},
                {"columnId": bridge_col_map["Measure Type"], "value": measure},
            ],
        })

    add_rows_to_sheet(MONTHLY_BRIDGE_SHEET_ID, rows)
    log.info(f"Created 3 rows for Task ID {record['Task ID']}")


def sync_rows():
    tracker_rows = get_tracker_rows()
    existing_ids = get_existing_task_ids()

    bridge_sheet = get_sheet(MONTHLY_BRIDGE_SHEET_ID)
    bridge_col_map = get_column_map(bridge_sheet["columns"])

    created = []

    for record in tracker_rows:
        task_id = record.get("Task ID")
        if task_id not in existing_ids:
            create_bridge_rows(record, bridge_col_map)
            created.append(task_id)

    log.info(f"Created rows for {len(created)} new tasks")
    return created


# --- FLASK ---

app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    body = request.get_json(silent=True) or {}

    challenge = body.get("challenge")
    if challenge:
        return jsonify({"smartsheetHookResponse": challenge}), 200

    try:
        created = sync_rows()
        log.info(f"Webhook sync created: {created}")
    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)

    return jsonify({"status": "ok"}), 200


@app.route("/sync", methods=["GET"])
def manual_sync():
    try:
        created = sync_rows()
        return jsonify({"created": created}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
