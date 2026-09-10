"""Order producer: generates random orders and sends them to the SQS queue.

Each running copy of this script represents one source of incoming orders, and
several copies are started at once during the demonstration so that orders are
created faster than the workers can process them. A producer is given an ID on
the command line so its output is identifiable when three of them are running
side by side:

    python producer.py P1
    python producer.py P1 20      # send only 20 orders, then stop

The producer never waits for an order to be processed. It hands the message to
SQS and immediately moves on to the next one, which is what "asynchronous"
means in practice and is the reason a slow set of workers can never slow down
the ordering side of this system.
"""

import json
import random
import sys
import time
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

import config


def build_order(producer_id, sequence):
    """Create one order as a plain dictionary, ready to be sent as JSON."""
    return {
        "order_id": f"{producer_id}-{sequence:04d}",
        "product": random.choice(config.PRODUCTS),
        "quantity": random.randint(1, 5),
        "producer": producer_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    producer_id = sys.argv[1] if len(sys.argv) > 1 else "P1"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    client = config.sqs_client()
    url = config.queue_url(client)
    config.log(producer_id, f"sending an order every {config.PRODUCER_DELAY}s (Ctrl+C to stop)")

    sent = 0
    try:
        while limit is None or sent < limit:
            order = build_order(producer_id, sent + 1)
            try:
                client.send_message(QueueUrl=url, MessageBody=json.dumps(order))
            except (ClientError, BotoCoreError) as exc:
                config.fail("send_message failed", exc)

            sent += 1
            config.log(producer_id, f"sent {order['order_id']}: {order['quantity']} x {order['product']}")
            time.sleep(config.PRODUCER_DELAY)
    except KeyboardInterrupt:
        pass

    config.log(producer_id, f"stopped after sending {sent} orders")


if __name__ == "__main__":
    main()
