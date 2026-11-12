"""Train a Random Forest from resolver query CSV files."""

import argparse
import csv
import json
import math
from pathlib import Path
import pickle

from ai.history import QueryHistory


def split_trace(path, horizon, history_window):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [
            (float(row["timestamp"]), row["domain"])
            for row in csv.DictReader(stream) if row["event"] == "QUERY_RECEIVED"
        ]
    if len(rows) < 2 or any(
        not math.isfinite(now) or not domain for now, domain in rows
    ) or any(a[0] > b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError(f"{path} needs chronological QUERY_RECEIVED events with domains")

    # Drop training labels that would need observations from the holdout period.
    boundary = rows[int(len(rows) * 0.8)][0]
    next_seen = {}
    targets = []
    for now, domain in reversed(rows):
        targets.append(int(next_seen.get(domain, math.inf) <= now + horizon))
        next_seen[domain] = now

    train, holdout = [], []
    history = QueryHistory(history_window)
    for (now, domain), target in zip(rows, reversed(targets)):
        history.observe(domain, now)
        example = (history.features(domain, now), target)
        if now + horizon < boundary:
            train.append(example)
        elif now >= boundary and now + horizon <= rows[-1][0]:
            holdout.append(example)
    if not train or not holdout:
        raise ValueError(f"{path} needs a longer trace for the {horizon:g}s horizon")
    return train, holdout


def train_model(paths, output, horizon=300.0, history_window=3600.0):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score

    if not (math.isfinite(horizon) and math.isfinite(history_window)
            and 0 < horizon <= history_window):
        raise ValueError("Use a positive horizon and a history window at least as long")
    train, holdout = [], []
    for path in paths:
        before, after = split_trace(path, horizon, history_window)
        train.extend(before)
        holdout.extend(after)

    train_features, train_labels = zip(*train)
    if len(set(train_labels)) != 2:
        raise ValueError("Training needs domains that repeat and domains that do not repeat")
    test_features, test_labels = zip(*holdout)
    model = RandomForestClassifier(n_estimators=40, min_samples_leaf=2, random_state=42)
    model.fit(train_features, train_labels)
    predictions = model.predict(test_features)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        pickle.dump({"model": model, "history_window": history_window, "horizon": horizon}, stream)
    return {
        "horizon_seconds": horizon,
        "history_window_seconds": history_window,
        "training_queries": len(train),
        "holdout_queries": len(holdout),
        "holdout_accuracy": accuracy_score(test_labels, predictions),
        "holdout_f1": f1_score(test_labels, predictions, zero_division=0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--horizon", type=float, default=300.0)
    parser.add_argument("--history-window", type=float, default=3600.0)
    args = parser.parse_args()
    try:
        metrics = train_model(args.logs, args.output, args.horizon, args.history_window)
    except (OSError, ValueError, KeyError, ImportError) as exc:
        parser.error(str(exc))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
