"""Summarize per-run measurements and plot mean with sample standard deviation."""

import argparse
import csv
from pathlib import Path
import statistics

from experiments.run import POLICIES, WORKLOADS, write_rows


METRICS = {
    "mean_ms": "Mean client latency (ms)",
    "p95_ms": "95th-percentile client latency (ms)",
    "hit_pct": "Cache hits (%)",
    "upstream_queries": "Upstream sends per run",
    "throughput_qps": "Throughput (queries/s)",
    "udp_payload_bytes": "UDP payload bytes per run",
}
COLORS = ("#708396", "#087E8B", "#D1653F")
LABELS = ("No cache", "LRU", "Random Forest")


def plot(directory):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    with (directory / "runs.csv").open(newline="", encoding="utf-8") as stream:
        runs = list(csv.DictReader(stream))
    summaries = []
    for workload in WORKLOADS:
        for policy in POLICIES:
            group = [row for row in runs if row["workload"] == workload and row["policy"] == policy]
            if not group:
                raise ValueError(f"No measurements for {workload} / {policy}")
            row = {"workload": workload, "policy": policy, "runs": len(group)}
            for metric in METRICS:
                values = [float(run[metric]) for run in group]
                row[f"{metric}_mean"] = statistics.mean(values)
                row[f"{metric}_sd"] = statistics.stdev(values) if len(values) > 1 else 0.0
            summaries.append(row)
    write_rows(directory / "summary.csv", summaries)

    fig, axes = plt.subplots(2, 3, figsize=(12.5, 7), facecolor="white")
    fig.suptitle("MiniDNS cache policy comparison", fontsize=20, fontweight="bold", y=0.98)
    fig.text(
        0.5, 0.928, "Local UDP · Means across runs · Error bars show one sample standard deviation",
        ha="center", color="#475569", fontsize=10,
    )
    for axis, (metric, title) in zip(axes.flat, METRICS.items()):
        for index, policy in enumerate(POLICIES):
            rows = [row for row in summaries if row["policy"] == policy]
            axis.bar(
                [position + (index - 1) * 0.24 for position in range(len(rows))],
                [row[f"{metric}_mean"] for row in rows],
                width=0.22, color=COLORS[index], capsize=3,
                yerr=[row[f"{metric}_sd"] for row in rows],
                error_kw={"elinewidth": 0.9, "ecolor": "#263547"},
            )
        axis.set_title(title, loc="left", fontsize=11, fontweight="bold")
        axis.set_xticks([0, 1], ["Repeated", "Mostly unique"])
        axis.set_ylim(bottom=0)
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#E2E8F0", linewidth=0.7)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.spines["bottom"].set_color("#CBD5E1")
        axis.tick_params(length=0, labelsize=9, pad=6)
    fig.legend(
        handles=[Patch(color=color, label=label) for color, label in zip(COLORS, LABELS)],
        loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.016),
    )
    fig.subplots_adjust(left=0.075, right=0.98, top=0.86, bottom=0.12, wspace=0.34, hspace=0.52)
    fig.savefig(directory / "comparison.png", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="experiment output directory containing runs.csv")
    args = parser.parse_args()
    try:
        plot(args.directory)
    except (OSError, ValueError, KeyError, ImportError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
