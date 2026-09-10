"""Order worker: receives orders from SQS, processes them, and deletes them.

Each running copy of this script is one consumer. The demonstration starts two
of them, lets the queue build up because the producers are faster, and then
starts a third to show the backlog draining more quickly. A worker is given an
ID on the command line so several can run side by side and be told apart:

    python worker.py C1
    FAIL_ONCE=1 python worker.py C2      # crash-and-retry demonstration

The order of operations matters and is the heart of SQS's reliability model:
the message is deleted only *after* the work has succeeded. If a worker dies
mid-processing, it never sends DeleteMessage, so once the queue's visibility
timeout expires SQS makes the message visible again and another worker picks it
up. Nothing is lost, at the cost of a message occasionally being delivered more
than once ("at-least-once delivery").
"""

import json
import os
import sys
import time

from botocore.exceptions import BotoCoreError, ClientError

import config

# When set, the worker abandons its first message instead of finishing it, to
# demonstrate visibility timeout and redelivery. See Phase 6 of the plan.
FAIL_ONCE = os.environ.get("FAIL_ONCE") == "1"


def process(worker_id, order):
    """Pretend to do the real work of fulfilling an order.

    A real system would charge a card or update inventory here; we just sleep,
    because the only property the demo needs is that processing takes longer
    than producing.
    """
    config.log(worker_id, f"processing {order['order_id']} ({order['quantity']} x {order['product']})")
    time.sleep(config.PROCESS_SECONDS)


def main():
    worker_id = sys.argv[1] if len(sys.argv) > 1 else "C1"

    client = config.sqs_client()
    url = config.queue_url(client)
    config.log(worker_id, f"waiting for orders, {config.PROCESS_SECONDS}s each (Ctrl+C to stop)")

    done = 0
    try:
        while True:
            try:
                response = client.receive_message(
                    QueueUrl=url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=config.WAIT_TIME_SECONDS,
                )
            except (ClientError, BotoCoreError) as exc:
                config.fail("receive_message failed", exc)

            # Long polling returned with nothing: the queue is empty right now.
            messages = response.get("Messages", [])
            if not messages:
                continue

            message = messages[0]

            # A message we cannot parse is a "poison message": if we simply
            # crashed, it would return after the visibility timeout and kill
            # the next worker too, forever. Real systems route these to a
            # dead-letter queue; we log and discard.
            try:
                order = json.loads(message["Body"])
            except (ValueError, KeyError):
                config.log(worker_id, f"discarding unreadable message: {message['Body'][:60]}")
                client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
                continue

            process(worker_id, order)

            if FAIL_ONCE:
                config.log(worker_id, f"SIMULATED CRASH before deleting {order['order_id']}")
                sys.exit(1)

            # Only now, after successful processing, remove it from the queue.
            # The receipt handle identifies this particular delivery of the
            # message, and is what DeleteMessage requires.
            try:
                client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
            except (ClientError, BotoCoreError) as exc:
                config.fail("delete_message failed", exc)

            done += 1
            config.record_processed(worker_id, order["order_id"])
            config.log(worker_id, f"done {order['order_id']} (total {done})")
    except KeyboardInterrupt:
        config.log(worker_id, f"stopped after processing {done} orders")


if __name__ == "__main__":
    main()
