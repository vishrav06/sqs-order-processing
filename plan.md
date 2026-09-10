# Plan — Scalable Asynchronous Order Processing System using Amazon SQS

## Context

This is a college mini-project demonstrating **why Amazon SQS is useful**: asynchronous
communication, decoupling producers from consumers, buffering bursts, and scaling
consumers horizontally. The central experiment is deliberately simple:

```
3 Producers --> [ SQS Queue ] --> Worker 1, Worker 2      => queue GROWS
                              --> + Worker 3 (started live) => queue DRAINS
```

The user is an AWS beginner. Everything up to and including AWS-CLI send/receive against
SQS already works. AWS state that exists today:

| Thing | Value |
|---|---|
| Region | `us-east-1` |
| Queue name | `sqs-order-processig-queue` (typo intentional — use verbatim) |
| Queue type | Standard |
| IAM user | `sqs-project-user` (in group `sqsProjectGroup`) |
| Local auth | AWS CLI configured; `aws sts get-caller-identity` works |

The project directory `C:\Users\vishr\Projects\sqs-mini-project` is empty (its single git
commit belongs to an unrelated project's history — we will just start committing here).

**Decisions confirmed with the user:**
- Connect to EC2 via **EC2 Instance Connect** (browser terminal, no `.pem` file).
- **Write and test code locally first**, then deploy to EC2 for the demo.
- **All 3 workers run as 3 processes on one EC2 instance.**

**Working style:** one step at a time, wait for confirmation before each major AWS step,
explain every AWS concept as it appears, explain each command's purpose, no extra AWS
services (no Lambda / ECS / RDS / DynamoDB / SNS), prefer 10 lines over 50.

---

## Phase 1 — EC2 setup (guided, console, one step at a time)

I walk the user through the AWS Console; the user does the clicking. After each numbered
step I stop and wait for confirmation before continuing. Concepts explained inline as they
come up: *region, AMI, instance type, security group, inbound vs outbound rules, IAM role
vs IAM user, instance profile, public IP*.

**1.1 — Create the IAM role for EC2** (do this *before* launching, so it can be attached
at launch)
- IAM → Roles → Create role → Trusted entity **AWS service** → **EC2**.
- Attach policy `AmazonSQSFullAccess` (acceptable for a student project; explain that
  production would scope it to the one queue ARN).
- Name: **actually created as `EC@-SQS-Role`** (typo, `@` for `2`; harmless, kept as-is —
  IAM roles cannot be renamed). Verified working: EC2 reports
  `arn:aws:sts::<account>:assumed-role/EC@-SQS-Role/i-0219b752...`. **Phase 1 COMPLETE.**
- **Concept to explain:** an IAM *role* is assumed by the machine, and AWS injects
  short-lived rotating credentials into the instance. This is why we never copy the access
  key onto EC2 — Boto3 finds these automatically. This satisfies the project's security rule.

**1.2 — Launch the EC2 instance**
- EC2 → Launch instance. Region must read **N. Virginia (us-east-1)** — same region as the
  queue.
- Name: `sqs-order-demo`
- AMI: **Amazon Linux 2023** (free tier eligible; ships with `dnf`, Python 3.9+)
- Instance type: **t2.micro** or **t3.micro** (whichever is marked *Free tier eligible*)
- Key pair: **Proceed without a key pair** (we are using Instance Connect)
- Network → Firewall (security group) → **Create new**, name `sqs-demo-sg`, with inbound rules:
  - `SSH` / TCP 22 / source **prefix list `com.amazonaws.us-east-1.ec2-instance-connect`**
    — NOT "My IP": the browser terminal connects from AWS's own network, not the user's IP,
    so "My IP" would lock us out. Fallback if the prefix list is hard to find: `0.0.0.0/0`
    (safe here only because the instance has no key pair and password auth is disabled).
  - `Custom TCP` / port **5000** / source **Anywhere (0.0.0.0/0)** — the Flask app. I will
    explain the risk plainly and note that "My IP" is the safer choice if their IP is stable.
- Advanced details → **IAM instance profile** → `EC2-SQS-Role`
- Launch.

**1.3 — Connect and verify**
- Select instance → **Connect** → *EC2 Instance Connect* tab → Connect.
- Verify the environment (each command explained):
  ```bash
  python3 --version
  sudo dnf install -y python3-pip git
  pip3 install --user boto3 flask
  aws sts get-caller-identity      # should show .../EC2-SQS-Role/i-xxxx, NOT sqs-project-user
  aws sqs get-queue-url --queue-name sqs-order-processig-queue --region us-east-1
  ```
- The `get-caller-identity` output showing an **assumed-role** is the proof that the
  instance profile works and no keys are needed. This is a great viva talking point.

Deliverable of Phase 1: an EC2 instance that can reach the queue with zero credentials on disk.

---

## Phase 2 — Minimal SQS app (local first)

Files created in `C:\Users\vishr\Projects\sqs-mini-project\`. Every file gets a descriptive
multi-sentence module docstring; non-obvious functions get short docstrings (per the user's
standing preference). One file at a time, pausing for review.

- `config.py` — queue name, region, `QUEUE_URL` resolved once via
  `boto3.client("sqs").get_queue_url(...)`; processing-delay and producer-rate constants
  read from env vars with sane defaults. No credentials anywhere.
- `producer.py` — takes a producer id argv (`python producer.py P1`), builds a JSON order
  `{order_id, product, quantity, producer, created_at}` and calls `send_message` in a loop
  at a configurable rate; prints one line per send.
- `worker.py` — takes a worker id argv (`python worker.py C1`), long-polls with
  `receive_message(WaitTimeSeconds=20, MaxNumberOfMessages=1)`, sleeps `PROCESS_SECONDS` to
  simulate work, prints what it processed, then `delete_message(ReceiptHandle=...)`.

Consistent error handling across all files: a single `botocore.exceptions.ClientError`
handler pattern reused everywhere, and consistent print/log formatting
(`[C1] processing order 42 ...`) so the demo terminals read clearly on video.

**Gate:** run `python producer.py P1` then `python worker.py C1` locally against the real
queue and confirm send → receive → delete before writing any Flask code.

---

## Phase 3 — Flask backend

`app.py`, small and flat, same error-handling pattern:
- `POST /orders` — accept `{product, quantity}`, generate an order id, `send_message`,
  return `202` with the id. **Does no processing** — this is the decoupling point and the
  key architectural claim of the presentation.
- `GET /stats` — call `get_queue_attributes` for `ApproximateNumberOfMessages` (waiting) and
  `ApproximateNumberOfMessagesNotVisible` (in flight, i.e. currently being worked on), plus
  in-process counters for submitted/processed. These two SQS attributes *are* the queue-depth
  graph for the demo.
- `GET /` — serves the static page.
- Run with `flask run --host=0.0.0.0 --port=5000` so it is reachable from outside EC2
  (explain: `0.0.0.0` means "listen on all network interfaces", not just localhost).

---

## Phase 4 — Minimal frontend

`templates/index.html` + a little inline JS. Deliberately plain:
- Product + quantity inputs and a **Place Order** button → `POST /orders`, shows the returned
  order id.
- A stats strip polling `GET /stats` every second: **submitted / in queue / in flight /
  processed**.
- Because it polls once a second, the "in queue" number visibly climbs during the buildup
  phase and falls after Worker 3 starts — that number is the money shot of the video.
No CSS framework, no build step.

---

## Phase 5 — Deploy to EC2 and run the core experiment

- Push the repo to GitHub, `git clone` on the instance (simplest transfer given no `.pem`
  for `scp`). Fallback if GitHub auth is annoying: paste files via `cat > file <<'EOF'`.
- Install `tmux` (`sudo dnf install -y tmux`) so several producers/workers/Flask can run in
  visible panes in a single Instance Connect session — otherwise each background process
  needs its own browser tab.
- Tune the rates so the imbalance is obvious on camera: e.g. 3 producers at ~1 order/sec
  each (~3/sec in) vs workers taking ~2s per order (~0.5/sec each). Two workers = 1/sec out
  against 3/sec in ⇒ backlog grows fast; a third worker is not enough to fully drain, so the
  numbers will be tuned during a dry run until the drain is clearly visible within ~30s.
- Demo sequence: start Flask → start C1, C2 → start P1, P2, P3 → watch queue depth climb →
  stop producers → start C3 → watch it drain faster.

---

## Phase 6 — Failure / retry demonstration

Add an env-var flag to `worker.py` (e.g. `FAIL_ONCE=1`) that makes a worker print
"simulating crash" and exit **after receiving but before `delete_message`**. Then:
- the message is not deleted,
- its **visibility timeout** (set on the queue, we will confirm/set it to ~30s) expires,
- the message becomes visible again and another worker picks it up.

This is the concrete demonstration of **at-least-once delivery** and of *why you delete only
after successful processing*. Watching `ApproximateNumberOfMessagesNotVisible` drop back into
`ApproximateNumberOfMessages` on the dashboard makes it visible without reading logs.

---

## Phase 7 — Deliverables

- `README.md` — architecture, setup steps, how to run the demo.
- `docs/architecture.md` — the ASCII diagram from the brief, plus a rendered image for slides.
- `docs/viva.md` — Q&A covering every concept in the brief's checklist (AWS/region/IAM/EC2/
  security groups/SSH; SQS queue, message, producer, consumer, async, decoupling, buffering,
  SendMessage/ReceiveMessage/DeleteMessage, ReceiptHandle, visibility timeout, at-least-once,
  Standard vs FIFO, consumer scaling, consumer crash; Boto3 and how auth works).
- `docs/script.md` — 5–7 min voiceover script matching the brief's timing breakdown, written
  in a plain student voice, following the story arc (problem → bottleneck → SQS as buffer →
  scale consumers → reliability), not a list of definitions.
- Slides outline in `docs/slides.md`.

---

## Verification

| Stage | How we prove it works |
|---|---|
| Phase 1 | `aws sts get-caller-identity` on EC2 returns the **assumed-role** ARN; `get-queue-url` succeeds |
| Phase 2 | Run producer then worker locally; a message is sent, received, printed, deleted; `ApproximateNumberOfMessages` returns to 0 |
| Phase 3 | `curl -X POST .../orders` returns an order id and the queue count increments |
| Phase 4 | Load `http://<ec2-public-ip>:5000` from the Windows browser; submit an order; stats update |
| Phase 5 | Queue depth visibly rises with 2 workers and visibly falls after starting C3 — recorded for the video |
| Phase 6 | Killed worker's message reappears after the visibility timeout and is processed by another worker |

## Cost / cleanup note

t2.micro + SQS free-tier (1M requests/month) keeps this at ~$0, but the instance bills if
left running past the free tier. Final step of the project: **stop or terminate the instance**
after recording the video — will be flagged again at that point.
