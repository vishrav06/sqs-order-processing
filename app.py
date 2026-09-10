"""Flask backend: accepts orders from the web page and puts them on the queue.

This is the decoupling point of the whole system, and the most important thing
about it is what it does *not* do. It never processes an order. It validates
the request, sends one SQS message, and replies immediately with an order ID,
typically in a few milliseconds no matter how overloaded the workers are. A
user placing an order is therefore never made to wait for warehouse work, and
the site cannot be brought down by a slow or crashed worker.

It also serves the dashboard and a /stats endpoint that reports live queue
depth, which is what makes the build-up-and-drain experiment visible on screen.

Run it with:

    flask --app app run --host=0.0.0.0 --port=5000
"""

from flask import Flask, jsonify, render_template, request
from botocore.exceptions import BotoCoreError, ClientError

import json
from datetime import datetime, timezone

import config

app = Flask(__name__)

client = config.sqs_client()
QUEUE_URL = config.queue_url(client)

# Orders submitted through the web page since the server started. Producers
# running as separate scripts are not counted here; the queue depth from SQS
# covers those.
submitted = 0


@app.route("/")
def index():
    """Serve the single-page dashboard."""
    return render_template("index.html")


@app.route("/orders", methods=["POST"])
def create_order():
    """Accept an order, put it on the queue, and return its ID immediately."""
    global submitted

    data = request.get_json(silent=True) or {}
    product = str(data.get("product", "")).strip()
    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 0

    if not product or quantity < 1:
        return jsonify({"error": "product is required and quantity must be at least 1"}), 400

    submitted += 1
    order = {
        "order_id": f"WEB-{submitted:04d}",
        "product": product,
        "quantity": quantity,
        "producer": "web",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        client.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(order))
    except (ClientError, BotoCoreError) as exc:
        submitted -= 1
        return jsonify({"error": f"could not queue order: {exc}"}), 502

    # 202 Accepted, not 200 OK: the order is queued, not yet fulfilled. This is
    # the honest HTTP status code for asynchronous work.
    return jsonify({"order_id": order["order_id"], "status": "queued"}), 202


@app.route("/stats")
def stats():
    """Report live queue depth and completion counts for the dashboard."""
    try:
        attributes = client.get_queue_attributes(
            QueueUrl=QUEUE_URL,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
    except (ClientError, BotoCoreError) as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({
        "submitted_here": submitted,
        "waiting": int(attributes["ApproximateNumberOfMessages"]),
        "in_progress": int(attributes["ApproximateNumberOfMessagesNotVisible"]),
        "processed": config.processed_count(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
