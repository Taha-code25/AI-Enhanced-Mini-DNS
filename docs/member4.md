# Member 4 run guide

Run these commands from the repository root. The logging milestone uses the
Python standard library. Training and plotting use the dependencies in
`requirements.txt`.

## 1. Capture and check resolver events

Run each of the first two commands in its own PowerShell terminal and leave
both running. Run the remaining commands in a third terminal.

```powershell
python -B -m authoritative.server
```

```powershell
python -B -m resolver.resolver --query-log results/query-log.csv
```

```powershell
python -B -m client.client portal.university.test --repeat 2 --log results/client-log.csv
python -B -m client.client missing.test
Import-Csv results/query-log.csv | Select-Object -Last 13 request_id,domain,event,status,cache_policy
```

With a fresh resolver, the first valid lookup is a cache miss and sends an
upstream request. The second is a cache hit. Both return `10.0.0.10`.
The missing name returns `NOT_FOUND` and a nonzero client exit code.
Resolver CSV rows use the client request ID for correlation, with a separate
upstream ID on upstream events. Each traffic event records UDP payload bytes.
The resolver appends to the selected log, so use a new path for each test.
Stop the resolver with Ctrl+C before starting the AI resolver below.

## 2. Train and try the AI cache

```powershell
python -m pip install -r requirements.txt
python -B -m experiments.run --train-only --output results/member4-training
Get-Content results/member4-training/model-metrics.json
```

This creates its own local UDP servers on ephemeral ports and collects two
synthetic training traces. To train on your own logs, run
`python -B -m ai.train results/query-log.csv --output results/model.pkl`.
With the default five-minute horizon, collect enough chronological history
for both training and holdout periods, including names that repeat and
names that do not. The synthetic experiment uses a shorter horizon to
match the accelerated request schedule. Load models you generated locally.

With the authoritative server running, start the AI resolver in its own
terminal. Run the client and inspect the log from another terminal.

```powershell
python -B -m resolver.resolver --ai-model results/member4-training/model.pkl --query-log results/ai-log.csv
```

```powershell
python -B -m client.client portal.university.test --repeat 2
Import-Csv results/ai-log.csv | Select-Object -Last 8 request_id,event,ttl,cache_policy
python -B -m unittest discover -s tests -p test_member4.py -v
```

Look for `cache_policy=ai`. The unit tests exercise model-based eviction,
TTL expiry, zero TTL, query history, and structured logging. The benchmark
uses capacity eight to trigger evictions under both cached policies.

## 3. Run the comparison and generate graphs

```powershell
python -B -m experiments.run --output results/member4-run
python -B -m experiments.plot results/member4-run
```

Choose a fresh output directory for each benchmark. By default it runs two
workloads, each with five repetitions of 120 queries per policy. Each
repetition replays the same domain sequence across all three policies and
starts each policy with a fresh resolver. The training traces use separate
random seeds. Add `--runs 1 --queries 80` for a quick smoke test.

- `runs.csv` contains each policy/workload run and `summary.csv` contains
  the mean and sample standard deviation across runs.
- `comparison.png` plots client latency, cache hits, upstream sends,
  throughput, and UDP payload bytes.
- `reliability.csv` records the time to receive `SERVFAIL`, the upstream
  attempt count, and the latency of the first successful lookup after the
  authority restarts. The resolver stays up throughout the probe.
- Run directories contain the exact domain sequence, client lookup CSV,
  and resolver event CSV. `model-metrics.json` records chronological
  holdout accuracy and F1.

The runner schedules queries 12 ms apart. Throughput includes this pacing.
Traffic totals count UDP application payloads observed at the resolver on
both legs, excluding UDP/IP headers and link overhead. The results describe
synthetic workloads over localhost. Deployment on separate hosts and packet
captures can be evaluated with the same policy and logging switches.
