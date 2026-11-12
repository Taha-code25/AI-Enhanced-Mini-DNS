"""Compare the three cache policies using the real MiniDNS UDP processes."""

import argparse
from contextlib import ExitStack, contextmanager
import csv
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import time

from ai.train import train_model
from client.client import DnsClient, append_log


ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
CAPACITY = 8
INTERVAL = 0.012
HORIZON = 0.15
HISTORY_WINDOW = 2.0
POLICIES = ("none", "lru", "ai")
WORKLOADS = {"repeated": 0.82, "mostly_unique": 0.22}
HOT = [f"hot-{index}.bench.test" for index in range(6)]


@contextmanager
def server(module, args, log_path, env, marker):
    with log_path.open("wb") as stream:
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", module, *map(str, args)],
            cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                output = log_path.read_text(encoding="utf-8", errors="replace")
                ready = re.search(re.escape(marker) + r" 127\.0\.0\.1:(\d+)", output)
                if ready:
                    yield int(ready.group(1))
                    return
                if process.poll() is not None:
                    break
                time.sleep(0.03)
            raise RuntimeError(f"{module} failed to start. See {log_path}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def authoritative(records, directory, port=0, label="authoritative"):
    return server(
        "authoritative.server", ("--bind-host", HOST, "--port", port, "--records", records),
        directory / f"{label}.log", os.environ.copy(), "Authoritative server listening on",
    )


def resolver(auth_port, directory, policy, model=None):
    env = dict(
        os.environ, MINIDNS_AUTH_HOST=HOST, MINIDNS_AUTH_PORT=str(auth_port),
        MINIDNS_RESOLVER_HOST=HOST, MINIDNS_RESOLVER_PORT="0",
        MINIDNS_CACHE_CAPACITY=str(0 if policy == "none" else CAPACITY),
        MINIDNS_UPSTREAM_TIMEOUT="0.2", MINIDNS_UPSTREAM_RETRIES="2",
    )
    args = ["--query-log", directory / "resolver.csv"]
    if policy == "ai":
        args.extend(("--ai-model", model))
    return server(
        "resolver.resolver", args, directory / "resolver.log", env, "Resolver listening on",
    )


def workload(name, count, seed):
    rng = random.Random(seed)
    return [
        rng.choices(HOT, weights=(6, 5, 4, 3, 2, 1))[0]
        if rng.random() < WORKLOADS[name] else f"cold-{index}.bench.test"
        for index in range(count)
    ]


def read_events(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def completed_events(path, request_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        rows = read_events(path)
        if rows and rows[-1]["request_id"] == str(request_id) and rows[-1]["event"] == "RESPONSE_SENT":
            return rows
        time.sleep(0.01)
    raise RuntimeError(f"Missing final response event in {path}")


def run_workload(auth_port, directory, policy, domains, expected, model=None):
    directory.mkdir()
    (directory / "workload.txt").write_text("\n".join(domains) + "\n", encoding="utf-8")
    with resolver(auth_port, directory, policy, model) as port:
        client = DnsClient(HOST, port, timeout=3, retries=0)
        start = time.perf_counter()
        lookups = []
        for index, domain in enumerate(domains):
            delay = start + index * INTERVAL - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            result = client.lookup(domain)
            if not result.ok or result.ip != expected[domain]:
                raise RuntimeError(f"{policy} returned {result} for {domain}. See {directory}")
            lookups.append(result)
        elapsed = time.perf_counter() - start
        rows = completed_events(directory / "resolver.csv", lookups[-1].request_id)
    for result in lookups:
        append_log(str(directory / "client.csv"), result)

    counts = {
        event: sum(row["event"] == event for row in rows)
        for event in ("QUERY_RECEIVED", "CACHE_HIT", "CACHE_MISS", "UPSTREAM_SENT", "RESPONSE_SENT")
    }
    total = len(domains)
    if (counts["QUERY_RECEIVED"] != total or counts["RESPONSE_SENT"] != total
            or counts["CACHE_HIT"] + counts["CACHE_MISS"] != total
            or any(row["cache_policy"] != policy for row in rows)):
        raise RuntimeError(f"Incomplete or inconsistent resolver telemetry in {directory}")
    latencies = sorted(result.latency_ms for result in lookups)
    return {
        "queries": total,
        "mean_ms": statistics.mean(latencies),
        "p95_ms": latencies[math.ceil(0.95 * total) - 1],
        "hit_pct": 100 * counts["CACHE_HIT"] / total,
        "upstream_queries": counts["UPSTREAM_SENT"],
        "throughput_qps": total / elapsed,
        "udp_payload_bytes": sum(int(row["udp_payload_bytes"] or 0) for row in rows),
    }


def reliability(records, directory, run):
    directory.mkdir()
    with ExitStack() as resolver_lifetime:
        with authoritative(records, directory) as auth_port:
            port = resolver_lifetime.enter_context(resolver(auth_port, directory, "none"))
            client = DnsClient(HOST, port, timeout=3, retries=0)
            if not client.lookup(HOT[0]).ok:
                raise RuntimeError("Reliability probe failed before the outage")
        error = client.lookup(HOT[0])
        if error.error_name != "SERVFAIL":
            raise RuntimeError(f"Expected SERVFAIL with the authority down, got {error}")
        with authoritative(records, directory, auth_port, label="authoritative-restarted"):
            recovered = client.lookup(HOT[0])
        if not recovered.ok:
            raise RuntimeError(f"Resolver failed to recover: {recovered}")
        rows = completed_events(directory / "resolver.csv", recovered.request_id)
    attempts = sum(
        row["event"] == "UPSTREAM_SENT" and row["request_id"] == str(error.request_id)
        for row in rows
    )
    if attempts != 3:
        raise RuntimeError(f"Expected three upstream attempts, observed {attempts}")
    return {
        "run": run,
        "error": error.error_name,
        "upstream_attempts": attempts,
        "failure_to_error_ms": error.latency_ms,
        "recovery_lookup_ms": recovered.latency_ms,
    }


def write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def experiment(output, runs, queries, train_only=False):
    output.mkdir(parents=True)
    domains = HOT + [f"cold-{index}.bench.test" for index in range(max(queries, 200))]
    records = output / "records.json"
    answers = {
        domain: f"10.0.{index // 254}.{index % 254 + 1}"
        for index, domain in enumerate(domains)
    }
    records.write_text(json.dumps([
        {"domain": domain, "ip": ip, "ttl": 300} for domain, ip in answers.items()
    ], indent=2) + "\n", encoding="utf-8")

    with authoritative(records, output) as auth_port:
        training_logs = []
        for index, name in enumerate(WORKLOADS):
            directory = output / f"training-{name}"
            run_workload(auth_port, directory, "lru", workload(name, max(queries, 200), 314 + index), answers)
            training_logs.append(directory / "resolver.csv")
        model = output / "model.pkl"
        metrics = train_model(training_logs, model, HORIZON, HISTORY_WINDOW)
        (output / "model-metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        print(f"Trained model on {metrics['training_queries']} queries.", flush=True)
        if train_only:
            return
        results = []
        for index, name in enumerate(WORKLOADS):
            for repetition in range(runs):
                trace = workload(name, queries, 1000 + 100 * index + repetition)
                order = POLICIES[repetition % 3:] + POLICIES[:repetition % 3]
                for policy in order:
                    directory = output / f"{name}-{repetition + 1}-{policy}"
                    measured = run_workload(auth_port, directory, policy, trace, answers, model)
                    results.append({"workload": name, "run": repetition + 1, "policy": policy, **measured})
                print(f"{name} run {repetition + 1}/{runs}: all three policies completed.", flush=True)
    write_rows(output / "runs.csv", results)
    probes = [reliability(records, output / f"reliability-{run}", run) for run in range(1, runs + 1)]
    write_rows(output / "reliability.csv", probes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for raw logs and results")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--train-only", action="store_true", help="collect the training traces and save a model")
    args = parser.parse_args()
    if args.runs < 1 or not 80 <= args.queries <= 10000:
        parser.error("Use at least one run and 80 to 10,000 queries per workload")
    try:
        experiment(args.output.resolve(), args.runs, args.queries, args.train_only)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
