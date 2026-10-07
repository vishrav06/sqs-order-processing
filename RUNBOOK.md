# Demo Runbook

Step-by-step commands for running the SQS order processing demo on EC2, in the
order you need them. See [README.md](README.md) for how the system works.

---

## 1. First-time setup (once per EC2 instance)

Connect: **EC2 Console → instance → Connect → EC2 Instance Connect**.

```bash
sudo dnf install -y python3-pip git tmux
git clone https://github.com/vishrav06/sqs-order-processing.git
cd sqs-order-processing
pip3 install --user boto3 flask
aws sts get-caller-identity      # must show "assumed-role/..." (IAM role works)
```

---

## 2. Before every run (clean start)

If the instance was stopped, start it in the console first. **The public IP
changes after every stop/start.**

```bash
cd ~/sqs-order-processing
tmux kill-server                                   # "no server running" / "error connecting" is fine
pkill -f producer.py; pkill -f worker.py; pkill -f app.py
git pull                                           # get latest code
export PYTHONWARNINGS=ignore                       # needed in every new session

# Empty the queue and reset the "Processed" counter
aws sqs purge-queue --region us-east-1 --queue-url $(aws sqs get-queue-url \
  --queue-name sqs-order-processig-queue --region us-east-1 --query QueueUrl --output text)
rm -f processed.log
```

Wait ~60 seconds after purging (purge is allowed once per 60s and takes up to a
minute to finish).

Check packages if anything fails to import:

```bash
python3 -c "import boto3, flask; print('ok')"     # if error: pip3 install --user boto3 flask
```

---

## 3. Start tmux and the backend

```bash
tmux
tmux set -g mouse on        # click panes, drag borders, scroll with the wheel
python3 app.py
```

Flask prints `127.0.0.1` and `172.31.x.x` — **neither is the public IP.** Get it
from the EC2 console (**Public IPv4 address**) or from another pane:

```bash
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4; echo
```

Open **`http://<public-ip>:5000`** in the browser (type `http://`, not `https://`).
If it doesn't load, port 5000 is not open in the security group.

---

## 4. The core experiment: 2 workers → 3 producers → add a 3rd worker

Open a new pane for each step (`Ctrl+B` then `"` or `%` — see section 7).

**Step 1 — two workers**

```bash
python3 worker.py C1
```
```bash
python3 worker.py C2
```

**Step 2 — three producers, all in one pane** (`Ctrl+C` stops all three)

```bash
(trap 'kill 0' INT; python3 producer.py P1 & python3 producer.py P2 & python3 producer.py P3 & wait)
```

Dashboard: **Waiting in queue** climbs ~2/sec, **Being processed** stays at 2.
(~3 orders/sec in, ~1 order/sec out.)

**Step 3 — add a third worker**

Either by hand, after ~1 minute:

```bash
python3 worker.py C3
```

Or automatically, with the autoscaler (start it right after the producers):

```bash
python3 autoscaler.py                   # adds C3 at 200+ waiting, removes it when the queue is empty
SCALE_AT=100 python3 autoscaler.py      # different scale-up threshold
```

It prints the queue depth every 5 seconds. With the default timings, 200 is
reached after roughly 1.5–2 minutes and it prints `SCALE UP: ... -> starting
worker C3`; C3's output follows in the same pane. `Ctrl+C` stops both the
autoscaler and C3.

| Variable | Default | Meaning |
|---|---|---|
| `SCALE_AT` | `200` | Waiting orders that start C3 |
| `SCALE_DOWN_AT` | `0` | Waiting orders at or below which C3 is removed |
| `IDLE_CHECKS` | `3` | Consecutive low readings before removing C3 (3 × 5s = 15s) |
| `CHECK_SECONDS` | `5` | How often the queue is checked |

Dashboard: **Being processed** goes 2 → 3. More workers: `C4`, `C5`, … (each
clears ~0.5 orders/sec).

**Step 4 — stop the producers**

Click the producers pane and press `Ctrl+C`. The backlog drains to 0.

If the autoscaler is running, ~15 seconds after the queue hits 0 it prints
`SCALE DOWN: backlog cleared -> stopping worker C3`. C3 finishes the order it is
on, prints `shut down gracefully after processing N orders`, and **Being
processed** drops 3 → 2. The autoscaler keeps watching — start the producers
again and it will scale up again at 200.

**Step 5 — web form**

Place an order on the dashboard: it returns `HTTP 202` instantly and a worker
picks it up within seconds.

### Running several processes in one pane

| Command | Stops with |
|---|---|
| `python3 producer.py P1 & python3 producer.py P2` | `Ctrl+C` stops only P2; then `pkill -f producer.py` |
| `(trap 'kill 0' INT; python3 producer.py P1 & python3 producer.py P2 & wait)` | `Ctrl+C` stops both |
| `(trap 'kill 0' INT; python3 worker.py C1 & python3 worker.py C2 & wait)` | `Ctrl+C` stops both workers |
| `python3 worker.py C3 &` (in a pane at a prompt) | `pkill -f "worker.py C3"` |

---

## 5. Failure / retry demonstration

Stop all workers first (`Ctrl+C` in their panes, or `pkill -f worker.py`).

```bash
FAIL_ONCE=1 python3 worker.py C2      # pane A: will crash before deleting its first message
```
```bash
python3 producer.py P1 1              # pane B: send exactly one order
```

C2 prints `SIMULATED CRASH`. Dashboard: **Waiting 0, Being processed 1** — the
message is invisible, not lost.

```bash
python3 worker.py C1                  # pane A again: healthy worker
```

After ~20 seconds (visibility timeout) C1 receives the same order and completes
it. Note: C2 had already processed it before crashing, so it was processed twice
— SQS guarantees at-least-once delivery, not exactly-once.

---

## 6. Optional: whole demo with one script

Create once (outside tmux):

```bash
cd ~/sqs-order-processing
cat > demo.sh <<'EOF'
#!/bin/bash
# ./demo.sh               start the demo
# ./demo.sh reset         purge the queue and reset counters first
# SCALE_AT=100 ./demo.sh  start worker C3 at 100 waiting orders (default 200)
cd "$(dirname "$0")" || exit 1
DIR=$(pwd)
export PYTHONWARNINGS=ignore

if [ -n "$TMUX" ]; then
  echo "Run this outside tmux: run 'tmux kill-server' first, then ./demo.sh"
  exit 1
fi

pkill -f producer.py; pkill -f worker.py; pkill -f app.py
tmux kill-session -t demo 2>/dev/null

if [ "$1" = "reset" ]; then
  echo "Purging the queue and resetting counters (about 60 seconds)..."
  URL=$(aws sqs get-queue-url --queue-name sqs-order-processig-queue \
        --region us-east-1 --query QueueUrl --output text) || exit 1
  aws sqs purge-queue --queue-url "$URL" --region us-east-1 || exit 1
  rm -f processed.log
  sleep 60
fi

run()   { tmux send-keys -t "$1" "$2" C-m; }
split() { tmux split-window -t demo -c "$DIR" -P -F '#{pane_id}'; tmux select-layout -t demo tiled >/dev/null; }

tmux new-session -d -s demo -c "$DIR" -x "$(tput cols)" -y "$(tput lines)"
tmux set -g mouse on

APP=$(tmux display -p -t demo '#{pane_id}')
run "$APP" "python3 app.py"
sleep 2
C1=$(split); run "$C1" "python3 worker.py C1"
C2=$(split); run "$C2" "python3 worker.py C2"
sleep 2
P=$(split)
run "$P" "(trap 'kill 0' INT; python3 producer.py P1 & python3 producer.py P2 & python3 producer.py P3 & wait)"
AS=$(split)
run "$AS" "python3 autoscaler.py"

tmux attach -t demo
EOF
chmod +x demo.sh
```

Run:

```bash
./demo.sh reset       # fresh start (waits ~60s for the purge)
./demo.sh             # start without resetting
```

---

## 7. tmux cheat sheet

Every shortcut is **`Ctrl+B`, release, then the key** (don't hold Ctrl for the
second key unless stated).

### Panes

| Action | Keys / command |
|---|---|
| Split side by side | `Ctrl+B` `%` |
| Split top / bottom | `Ctrl+B` `"` |
| Split (if `Ctrl+B` is captured by the browser) | `tmux split-window -h` / `tmux split-window -v` |
| Move to pane | `Ctrl+B` + arrow key, or click (mouse mode) |
| Next pane | `Ctrl+B` `o` |
| Jump by number | `Ctrl+B` `q`, then press the number quickly |
| Close pane | `Ctrl+C` the program, then `exit` (or `Ctrl+D`) |
| Force-close pane | `Ctrl+B` `x`, then `y` |

The active pane has a **green border**.

### Resizing

| Action | Keys / command |
|---|---|
| Resize by 1 cell | `Ctrl+B`, then **hold Ctrl** + arrow key (repeat) |
| Resize by 5 cells | `Ctrl+B`, then **Alt** + arrow key |
| Resize by mouse | Drag the pane border (needs `tmux set -g mouse on`) |
| Resize by command | `tmux resize-pane -L 10` / `-R 10` / `-U 5` / `-D 5` |
| Fullscreen one pane / undo | `Ctrl+B` `z` |
| Cycle preset layouts | `Ctrl+B` `Space` |
| Even grid | `tmux select-layout tiled` |
| All side by side | `tmux select-layout even-horizontal` |
| All stacked | `tmux select-layout even-vertical` |
| One big pane on the left | `tmux select-layout main-vertical` |

**"no space for new pane"** — run `tmux select-layout tiled`, then split again;
or zoom the browser out (`Ctrl` `-`, reset with `Ctrl` `0`) so more fits; or
run several processes in one pane (section 4); or use a second window.

### Windows (extra full-size screens)

| Action | Keys |
|---|---|
| New window | `Ctrl+B` `c` |
| Next / previous window | `Ctrl+B` `n` / `Ctrl+B` `p` |
| Go to window 0, 1, … | `Ctrl+B` `0`, `Ctrl+B` `1`, … |

### Scrolling

| Action | Keys |
|---|---|
| Enter scroll mode | `Ctrl+B` `[` (yellow `[0/350]` appears top-right) |
| Scroll | Arrow keys, `PgUp` / `PgDn`, or mouse wheel (mouse mode) |
| Leave scroll mode | `q` |

A pane in scroll mode **stops updating** and ignores typing — the program keeps
running underneath. If a pane looks frozen, press `q`.

### Sessions

| Action | Keys / command |
|---|---|
| Detach (leave everything running) | `Ctrl+B` `d` |
| Reattach | `tmux attach` |
| Kill everything | `tmux kill-server` |

---

## 8. When finished

```bash
tmux kill-server
```

Then **stop or terminate the EC2 instance** in the console (it bills by the hour
after the free tier).

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `tmux: command not found` | `sudo dnf install -y tmux` |
| `AWS ERROR: could not find queue` | Wrong region, queue name, or IAM role not attached |
| `Address already in use` starting Flask | Old app still running: `pkill -f app.py` |
| Browser can't reach `:5000` | Port 5000 not in the security group, or private IP used |
| Red dot / "Disconnected" on dashboard | `app.py` stopped or missing SQS permissions |
| "Processed" stays at 0 | Workers started from a different folder than `app.py` |
| Pane frozen | It's in scroll mode — press `q` |
| `purge-queue` error | Already purged in the last 60s — wait and retry |
