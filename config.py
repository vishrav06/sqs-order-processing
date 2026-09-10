"""Shared configuration and AWS helpers for the SQS order processing demo.

Every other module in this project (the producer, the worker, and the Flask
backend) imports from here so that the region, the queue name, the timing knobs
and the error handling stay identical across all of them. Nothing in this file
contains credentials: Boto3 discovers them automatically, from the AWS CLI
configuration when running on the local machine, and from the attached IAM role
when running on the EC2 instance. That is the whole point of using an instance
profile, and it means the same source code runs unchanged in both places.

The demo's behaviour is tuned entirely through environment variables listed
below, so the producer/consumer imbalance can be adjusted during the live
demonstration without editing any code.
"""

import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# --- Fixed AWS settings -------------------------------------------------
# The queue name is spelled exactly as it exists in the AWS account, typo and
# all. The region must match the queue's region: SQS is a regional service, so
# the same queue name in another region is a different (non-existent) queue.
REGION = os.environ.get("AWS_REGION", "us-east-1")
QUEUE_NAME = os.environ.get("QUEUE_NAME", "sqs-order-processig-queue")

# --- Demo timing knobs --------------------------------------------------
# PRODUCER_DELAY: seconds a producer waits between orders. Lower = faster input.
# PROCESS_SECONDS: seconds a worker pretends to spend processing one order.
# With 3 producers at 1.0s and workers at 2.0s, orders arrive at ~3/sec while
# each worker clears only ~0.5/sec, so two workers fall behind and the queue
# grows. This imbalance is the core experiment.
PRODUCER_DELAY = float(os.environ.get("PRODUCER_DELAY", "1.0"))
PROCESS_SECONDS = float(os.environ.get("PROCESS_SECONDS", "2.0"))

# Long polling: how long ReceiveMessage waits for a message before returning
# empty. Waiting instead of returning immediately means far fewer API calls and
# no busy-loop, which keeps us inside the SQS free tier during the demo.
WAIT_TIME_SECONDS = int(os.environ.get("WAIT_TIME_SECONDS", "10"))

PRODUCTS = ["Laptop", "Phone", "Headphones", "Monitor", "Keyboard"]


def sqs_client():
    """Return a Boto3 SQS client for the configured region."""
    return boto3.client("sqs", region_name=REGION)


def queue_url(client):
    """Look up the queue's URL from its name, exiting with advice if it fails.

    The URL is what every SQS API call actually addresses; resolving it at
    startup rather than hardcoding it keeps the account ID out of the source.
    """
    try:
        return client.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    except (ClientError, BotoCoreError) as exc:
        fail(f"could not find queue '{QUEUE_NAME}' in {REGION}", exc)


def fail(context, exc):
    """Print a consistent one-line AWS error message and exit.

    Used by every module so that a permissions problem or a wrong region looks
    the same no matter which part of the system hit it.
    """
    print(f"AWS ERROR: {context}\n  -> {exc}", file=sys.stderr)
    sys.exit(1)


# Workers and the Flask backend are separate processes and share no memory, so
# the count of completed orders is kept in a small shared file: each worker
# appends one line per finished order and the backend counts the lines.
PROCESSED_LOG = os.environ.get("PROCESSED_LOG", "processed.log")


def record_processed(worker_id, order_id):
    """Append one line recording a completed order, for the dashboard to count."""
    with open(PROCESSED_LOG, "a") as handle:
        handle.write(f"{worker_id} {order_id}\n")


def processed_count():
    """Return how many orders have been completed since the log was last reset."""
    try:
        with open(PROCESSED_LOG) as handle:
            return sum(1 for _ in handle)
    except FileNotFoundError:
        return 0


def log(tag, message):
    """Print a tagged line, e.g. '[C1] processed order 42'.

    Consistent prefixes matter here because the demo runs several producers and
    workers side by side in one screen recording.
    """
    print(f"[{tag}] {message}", flush=True)
