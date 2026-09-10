# Scalable Asynchronous Order Processing System using Amazon SQS

A small cloud application that demonstrates why message queues exist. Orders are
submitted through a web page, placed on an **Amazon SQS** queue, and processed by
worker processes running on an **Amazon EC2** instance. Because the workers are
deliberately slower than the producers, the queue visibly builds up a backlog —
and visibly drains again when another worker is added.

The point of the project is not the order processing. It is what the queue does
between the two halves of the system.

## What this demonstrates

| Concept | How it is shown |
|---|---|
| **Asynchronous communication** | The web API returns `202 Accepted` in milliseconds and never waits for an order to be fulfilled. |
| **Decoupling** | Producers do not know whether any worker exists. Workers can all be stopped and orders still get accepted. |
| **Buffering** | With 3 producers and 2 workers, a backlog of 100+ orders accumulates in the queue instead of being dropped. |
| **Consumer scaling** | Starting a third worker raises throughput by 50% and drains the backlog faster, with no code change. |
| **Reliability / retry** | A worker crashes before deleting its message; after the visibility timeout SQS redelivers it and another worker completes it. Nothing is lost. |

## Architecture

```
                         USER
                           |
                           v
                  +----------------+
                  | Minimal Web UI |
                  +-------+--------+
                          |
                        HTTP
                          |
                          v
                  +----------------+
                  |      EC2       |
                  | Flask backend  |
                  +-------+--------+
                          |
                    SendMessage
                          |
                          v
                  +----------------+
                  |   Amazon SQS   |
                  |  Order Queue   |
                  +-------+--------+
                          |
                   ReceiveMessage
                          |
              +-----------+-----------+
              |           |           |
              v           v           v
          Worker 1    Worker 2    Worker 3
                                      ^
                              added during demo
```

The message lifecycle inside SQS:

```
SendMessage  ->  message waits in queue  ->  ReceiveMessage (message becomes
invisible for the visibility timeout)  ->  worker processes it  ->  DeleteMessage
                                                    |
                                   worker crashes before deleting
                                                    |
                                     visibility timeout expires
                                                    |
                                    message becomes visible again
```

## Files

| File | Purpose |
|---|---|
| `config.py` | Region, queue name, timing knobs, shared AWS helpers and error handling. |
| `producer.py` | Sends orders to the queue. Run several copies to create load. |
| `worker.py` | Receives, processes and deletes orders. Run several copies to scale. |
| `app.py` | Flask backend: `POST /orders`, `GET /stats`, serves the dashboard. |
| `templates/index.html` | Dashboard: place an order, watch live queue depth. |
| `plan.md` | Development plan and progress log. |

## AWS setup

**Region:** `us-east-1` (N. Virginia). SQS is a regional service — the queue, the
EC2 instance and the code must all agree on the region.

**Queue:** a **Standard** queue named `sqs-order-processig-queue` (the spelling is
intentional; it matches the queue that exists in the account). Standard queues give
effectively unlimited throughput in exchange for approximate ordering and
at-least-once delivery, which suits independent orders. A FIFO queue would
guarantee exact order and exactly-once processing, at lower throughput.

**Visibility timeout:** 20 seconds. It must be longer than the worst-case
processing time (2 seconds here), otherwise a message would be redelivered while a
healthy worker is still working on it.

**Credentials — no keys anywhere in this repository:**

- On a laptop, Boto3 reads the AWS CLI configuration (`aws configure`).
- On EC2, an **IAM role** (`EC@-SQS-Role`) is attached to the instance. AWS injects
  temporary, automatically rotating credentials via the instance metadata service,
  and Boto3 finds them with no configuration at all.

Confirm the role is working on the instance with:

```bash
aws sts get-caller-identity
# "Arn": "arn:aws:sts::<account>:assumed-role/EC@-SQS-Role/i-0219b752..."
```

An `assumed-role` ARN (rather than a `user` ARN) is the proof that no access key is
stored on the machine. This is why the repository is safe to make public.

**Security group:** inbound SSH (22) for EC2 Instance Connect, and TCP 5000 for the
Flask app. Everything else is denied inbound; outbound is open by default, which is
how the instance reaches SQS.

## Running it

### On EC2

```bash
sudo dnf install -y python3-pip git tmux
git clone https://github.com/vishrav06/sqs-order-processing.git
cd sqs-order-processing
pip3 install --user boto3 flask
export PYTHONWARNINGS=ignore     # AL2023 ships Python 3.9; silences a boto3 notice
```

Start the backend, then open `http://<ec2-public-ip>:5000` in a browser:

```bash
python3 app.py
```

### The core experiment

Run each of these in its own tmux pane (`Ctrl+B %` to split vertically,
`Ctrl+B "` horizontally, `Ctrl+B` + arrow to move between panes):

```bash
python3 worker.py C1
python3 worker.py C2

python3 producer.py P1
python3 producer.py P2
python3 producer.py P3
```

Three producers send about 3 orders/second; each worker clears about 0.5/second.
Two workers therefore fall behind by roughly 2 orders per second and the
**Waiting in queue** figure on the dashboard climbs steadily.

After a minute or so, add a third worker:

```bash
python3 worker.py C3
```

**Being processed** goes from 2 to 3 — the dashboard shows the scale-up directly.
Then stop the producers with `Ctrl+C` and the backlog drains to zero.

This models a traffic burst: demand spikes above capacity, the queue absorbs it
without dropping anything, extra workers clear the backlog once the spike passes.

### The failure / retry demonstration

```bash
FAIL_ONCE=1 python3 worker.py C2     # abandons its first message before deleting
python3 producer.py P1 1             # send one order
```

The worker processes the order, prints `SIMULATED CRASH`, and exits without calling
`DeleteMessage`. The dashboard then shows **Waiting in queue: 0, Being processed: 1**
— the order is invisible, neither available nor complete. Start a healthy worker:

```bash
python3 worker.py C1
```

It receives nothing at first, then picks the order up once the 20-second visibility
timeout expires, and completes it. No retry logic was written anywhere in this
project; SQS did it.

The honest tradeoff: had the worker crashed *after* processing but *before*
deleting, the order would be processed twice. Standard SQS guarantees at-least-once
delivery, not exactly-once, which is why real systems make processing idempotent.

### Resetting between runs

```bash
aws sqs purge-queue --queue-url $(aws sqs get-queue-url \
  --queue-name sqs-order-processig-queue --region us-east-1 \
  --query QueueUrl --output text) --region us-east-1
rm -f processed.log
```

Purge is allowed only once every 60 seconds and may take up to a minute to finish.

## Tuning

All timings are environment variables read in `config.py`:

| Variable | Default | Effect |
|---|---|---|
| `PRODUCER_DELAY` | `1.0` | Seconds between orders per producer. Lower = more load. |
| `PROCESS_SECONDS` | `2.0` | Simulated work per order. Higher = slower workers. |
| `WAIT_TIME_SECONDS` | `10` | Long-poll duration for `ReceiveMessage`. |
| `FAIL_ONCE` | unset | `1` makes a worker abandon its first message. |

Long polling matters: with the default short polling, an idle worker would call the
API continuously and burn through the 1M-requests/month free tier during testing.
Long polling holds the connection open instead and returns the instant a message
arrives.

## Known limitations

- Flask's development server is used, not a production WSGI server like gunicorn.
- The completed-order count is kept in a local file (`processed.log`), which works
  because everything runs on one instance. A multi-instance deployment would use a
  database.
- The dashboard's "Submitted here" counter only counts orders placed through the web
  page, not those sent by `producer.py`; queue depth from SQS covers those.

## Cost

EC2 `t2.micro` and SQS (1M requests/month) are free-tier eligible, so this runs at
roughly zero cost. **Stop or terminate the instance when finished** — it bills by the
hour once the free tier is exhausted.
