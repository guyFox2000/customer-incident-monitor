import os
import re
import logging
import requests
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KEYWORDS = [
    "incident", "outage", "down", "sev1", "sev2", "sev3",
    "degraded", "alert", "critical", "urgent", "not working", "broken"
]
KEYWORD_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in KEYWORDS) + r')\b',
    re.IGNORECASE
)

MONITORED_CHANNELS = {
    "C094X3PKWET": "ext-cust-alerts-guardanthealth",
    "C08DVKJUTPF": "ext-cust-fuze",
    "C09UPJBDXLP": "ext-cust-glean",
    "C0A1ERYBCBC": "ext-cust-replit",
    "C08C9N2HTQD": "ext-cust-invisible",
    "C08SSR81LE6": "ext-cust-lumilens",
    "C08U8HSG5UJ": "ext-cust-guardanthealth",
    "C09JL91UDQV": "ext-cust-khoslaventures",
    "C093RM7QA59": "ext-cust-functionhealth",
    "C09GKKFFLT0": "ext-cust-forcepoint",
    "C09RT29PH61": "ext-cust-alerts-exaforce",
    "C09N0CFB76H": "ext-cust-alerts-khoslaventures",
    "C07RC763WLW": "ext-cust-exaforce",
    "C097BLFAETB": "ext-cust-alerts-equinix",
    "C09DS9GV347": "ext-cust-asteralabs",
    "C0AN749GR5W": "support-new-tickets",
}

GUY_SLACK_USER_ID = "U0ALET1KH6H"
PAGERDUTY_ROUTING_KEY = os.environ["PAGERDUTY_ROUTING_KEY"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

bolt_app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

def find_keyword(text):
    m = KEYWORD_PATTERN.search(text)
    return m.group(0).lower() if m else None

def trigger_pagerduty(channel_id, channel_name, keyword, text, sender, ts):
    severity = "critical" if keyword in ("sev1", "outage", "down") else "warning"
    payload = {
        "routing_key": PAGERDUTY_ROUTING_KEY,
        "event_action": "trigger",
        "dedup_key": f"customer-incident-{channel_id}-{ts}",
        "payload": {
            "summary": f"Customer Incident in #{channel_name}: keyword detected",
            "severity": severity,
            "source": f"Slack / #{channel_name}",
            "custom_details": {
                "channel": f"#{channel_name}",
                "channel_id": channel_id,
                "matched_keyword": keyword,
                "message_text": text[:1000],
                "sender": sender,
            }
        },
        "client": "Customer Incident Slack App",
    }
    resp = requests.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10)
    if resp.status_code == 202:
        logger.info(f"PagerDuty triggered: {resp.json().get('dedup_key')}")
    else:
        logger.error(f"PagerDuty error {resp.status_code}: {resp.text}")

def send_slack_dm(client, channel_name, keyword, text, ts, channel_id):
    from datetime import datetime, timezone
    import pytz
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    pt = dt.astimezone(pytz.timezone("America/Los_Angeles"))
    time_str = pt.strftime("%b %d %Y %I:%M %p PT")
    client.chat_postMessage(
        channel=GUY_SLACK_USER_ID,
        text=(
            f"Customer Incident Alert\n\nChannel: #{channel_name}\n"
            f"Keyword: {keyword}\nMessage: {text[:500]}\nTime: {time_str}"
        ),
    )

@bolt_app.event("message")
def handle_message(event, client, logger):
    channel_id = event.get("channel", "")
    subtype = event.get("subtype")
    bot_id = event.get("bot_id")
    text = event.get("text", "") or ""
    ts = event.get("ts", "0")
    user = event.get("user", "unknown")
    if channel_id not in MONITORED_CHANNELS:
        return
    if bot_id or subtype:
        return
    channel_name = MONITORED_CHANNELS[channel_id]
    keyword = find_keyword(text)
    if not keyword:
        return
    logger.info(f"Keyword in #{channel_name} from {user}")
    try:
        trigger_pagerduty(channel_id, channel_name, keyword, text, user, ts)
    except Exception as e:
        logger.error(f"PD failed: {e}")
    try:
        send_slack_dm(client, channel_name, keyword, text, ts, channel_id)
    except Exception as e:
        logger.error(f"DM failed: {e}")

flask_app = Flask(__name__)
handler = SlackRequestHandler(bolt_app)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
