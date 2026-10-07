"""Autoscaler: adds a worker when the backlog grows, removes it when it clears.

Instead of a person deciding when to add or remove the third worker, this
script watches the queue depth and does it automatically. This is the same idea
real AWS deployments use, where CloudWatch alarms on
ApproximateNumberOfMessagesVisible make an Auto Scaling group add instances
under load and remove them when it is idle, just reduced to a polling loop and
one extra process:

    python autoscaler.py                      # add C3 at 200 waiting, remove it at 0
    SCALE_AT=100 python autoscaler.py         # lower scale-up threshold

Scale-down only happens after the queue has stayed at or below SCALE_DOWN_AT
for IDLE_CHECKS checks in a row, so a momentary dip does not make the worker
flap on and off. The worker is stopped with SIGTERM, which it treats as "finish
the current order, then exit", so no order is abandoned mid-processing.

The extra worker runs as a child process, so its output appears in this pane
and Ctrl+C stops both the autoscaler and the worker.
"""

import os
import subprocess
import sys
import time

from botocore.exceptions import BotoCoreError, ClientError

import config

# SCALE_AT: backlog size that starts the extra worker.
# SCALE_DOWN_AT: backlog size at or below which the extra worker is removed.
# IDLE_CHECKS: consecutive low readings required before removing it.
# CHECK_SECONDS: how often the queue depth is checked.
# NEW_WORKER_ID: ID given to the extra worker.
SCALE_AT = int(os.environ.get("SCALE_AT", "200"))
SCALE_DOWN_AT = int(os.environ.get("SCALE_DOWN_AT", "0"))
IDLE_CHECKS = int(os.environ.get("IDLE_CHECKS", "3"))
CHECK_SECONDS = float(os.environ.get("CHECK_SECONDS", "5"))
NEW_WORKER_ID = os.environ.get("NEW_WORKER_ID", "C3")

WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")


def waiting_orders(client, url):
    """Return how many messages are waiting in the queue (approximate)."""
    try:
        attributes = client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )["Attributes"]
    except (ClientError, BotoCoreError) as exc:
        config.fail("get_queue_attributes failed", exc)
    return int(attributes["ApproximateNumberOfMessages"])


def main():
    client = config.sqs_client()
    url = config.queue_url(client)
    config.log("AS", f"scale up at {SCALE_AT}+ waiting, scale down at <= {SCALE_DOWN_AT} "
                     f"for {IDLE_CHECKS} checks (every {CHECK_SECONDS}s, Ctrl+C to stop)")

    worker = None
    low_readings = 0

    try:
        while True:
            # The extra worker may have exited on its own (e.g. an AWS error).
            if worker is not None and worker.poll() is not None:
                config.log("AS", f"worker {NEW_WORKER_ID} exited unexpectedly")
                worker = None

            depth = waiting_orders(client, url)
            state = "running" if worker else "not running"
            config.log("AS", f"{depth} orders waiting ({NEW_WORKER_ID} {state})")

            if worker is None and depth >= SCALE_AT:
                config.log("AS", f"SCALE UP: {depth} >= {SCALE_AT} -> starting worker {NEW_WORKER_ID}")
                worker = subprocess.Popen([sys.executable, WORKER_SCRIPT, NEW_WORKER_ID])
                low_readings = 0

            elif worker is not None and depth <= SCALE_DOWN_AT:
                low_readings += 1
                if low_readings >= IDLE_CHECKS:
                    config.log("AS", f"SCALE DOWN: backlog cleared -> stopping worker {NEW_WORKER_ID}")
                    worker.terminate()      # SIGTERM: finish current order, then exit
                    worker.wait()
                    worker = None
                    low_readings = 0

            else:
                low_readings = 0

            time.sleep(CHECK_SECONDS)
    except KeyboardInterrupt:
        # Ctrl+C reaches the worker too (same process group); wait for it to exit.
        if worker is not None:
            worker.wait()
        config.log("AS", "stopped")


if __name__ == "__main__":
    main()
