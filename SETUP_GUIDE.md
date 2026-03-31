# Synergy Monthly Bridge — Auto-Sync Setup Guide

This script automatically creates **Target / Forecast / Actual** rows in the **Synergy Monthly Bridge** sheet whenever someone checks the **"Synergy Initiative"** checkbox in the **PMI Synergy Tracker**.

---

## Prerequisites

- Python 3.8 or higher
- A Smartsheet account with API access
- A way to expose a local server to the internet (ngrok, or a cloud host)

---

## Step 1: Get your Smartsheet API Token

1. Log in to Smartsheet
2. Click your **profile icon** (bottom-left) → **Personal Settings**
3. Go to **API Access**
4. Click **Generate New Access Token**
5. Copy the token — you'll need it in Step 3

---

## Step 2: Install dependencies

Open a terminal in this folder and run:

```bash
pip install -r requirements.txt
```

---

## Step 3: Set environment variables

**On Mac/Linux:**

```bash
export SMARTSHEET_API_TOKEN="your-token-here"
export WEBHOOK_CALLBACK_URL="https://your-public-url/webhook"
export PORT=5000
```

**On Windows (PowerShell):**

```powershell
$env:SMARTSHEET_API_TOKEN = "your-token-here"
$env:WEBHOOK_CALLBACK_URL = "https://your-public-url/webhook"
$env:PORT = "5000"
```

> **Note:** The WEBHOOK_CALLBACK_URL must be a publicly accessible HTTPS URL.
> If running locally, use ngrok (see Step 4).

---

## Step 4: Make your server publicly accessible

Smartsheet needs to reach your server over the internet. The easiest way during development is **ngrok**:

```bash
# Install ngrok (if not already installed)
brew install ngrok      # Mac
# or download from https://ngrok.com/download

# Start ngrok tunnel
ngrok http 5000
```

ngrok will give you a URL like `https://abc123.ngrok-free.app`. Your callback URL is:

```
https://abc123.ngrok-free.app/webhook
```

Set this as your `WEBHOOK_CALLBACK_URL` environment variable.

---

## Step 5: Start the server

```bash
python synergy_bridge_sync.py
```

You should see:

```
Starting Synergy Bridge Sync server on port 5000
  Synergy Tracker Sheet: 8569817331093380
  Monthly Bridge Sheet:  7008991855988612
  Callback URL:          https://abc123.ngrok-free.app/webhook
  Endpoints:
    POST /webhook         - Smartsheet webhook receiver
    GET  /sync            - Manual sync trigger
    POST /setup-webhook   - Register the webhook with Smartsheet
    GET  /health          - Health check
```

---

## Step 6: Register the webhook with Smartsheet

With the server running, open a new terminal and run:

```bash
curl -X POST http://localhost:5000/setup-webhook
```

You should see:

```json
{"status": "created", "webhook_id": 123456789, "message": "Webhook created and enabled"}
```

Smartsheet will send a verification challenge to your `/webhook` endpoint.
The script handles this automatically.

---

## How it works

1. Someone checks **"Synergy Initiative"** on any row in the PMI Synergy Tracker
2. Smartsheet fires a webhook event to your server
3. The script reads the Tracker and finds all flagged sub-categories
4. It compares against what already exists in the Monthly Bridge
5. For any **new** sub-category, it creates 3 rows:
   - `[Sub-Category]` — **Target**
   - `[Sub-Category]` — **Forecast**
   - `[Sub-Category]` — **Actual**
6. The monthly value columns (Jan 26 – Dec 28) are left empty for manual input

---

## Manual sync (no webhook needed)

You can also trigger a sync manually at any time:

```bash
# Via browser or curl
curl http://localhost:5000/sync
```

This is useful for:
- Initial setup to backfill existing initiatives
- Testing that everything works before enabling the webhook
- Running as a cron job if you prefer polling over webhooks

---

## Hosting options for production

| Option | Difficulty | Cost | Notes |
|--------|-----------|------|-------|
| **ngrok** | Easy | Free tier available | Good for testing, URL changes on restart |
| **Railway** | Easy | ~$5/month | `railway up` deploys it |
| **Render** | Easy | Free tier available | Connect GitHub repo, auto-deploys |
| **AWS Lambda + API Gateway** | Medium | ~$0/month (free tier) | Serverless, always on |
| **Heroku** | Easy | ~$7/month | `git push heroku main` |
| **Your own server** | Medium | Varies | Full control |

For Kearney production use, **Render** or **Railway** are the simplest — just push the code to a GitHub repo and connect it.

---

## Troubleshooting

**"SMARTSHEET_API_TOKEN environment variable is not set"**
→ Make sure you exported the token before running the script

**Webhook returns 401**
→ Your API token may have expired. Generate a new one in Smartsheet settings

**"No new sub-categories to sync" but you expected rows**
→ Check that the "Synergy Initiative" checkbox is actually checked (not just the "At Risk" flag)

**Webhook not firing**
→ Run `curl http://localhost:5000/sync` to test the sync logic independently of the webhook

---

## Files

```
synergy_webhook/
├── synergy_bridge_sync.py   # Main script
├── requirements.txt         # Python dependencies
└── SETUP_GUIDE.md           # This file
```
