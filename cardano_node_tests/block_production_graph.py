#!/usr/bin/env python3
"""Generate a bar chart of total blocks per backend from an SQLite database.

The script retrieves the latest run_id from the runs table, aggregates the total
blocks per backend from the blocks table, and generates a bar chart saved as an image file.
"""

import argparse
import contextlib
import pathlib as pl
import sqlite3
import sys

import matplotlib.container as mcontainer
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


def get_latest_run_id(conn: sqlite3.Connection) -> str:
    """Return the latest run_id from the runs table.

    Assumes "latest" means last inserted row (highest rowid).
    """
    cur = conn.cursor()
    cur.execute("SELECT run_id FROM runs ORDER BY rowid DESC LIMIT 1;")
    row = cur.fetchone()
    if row is None:
        err = "No runs found in 'runs' table."
        raise RuntimeError(err)
    return str(row[0])


def get_blocks_per_backend(conn: sqlite3.Connection, *, run_id: str) -> list[tuple[str, int]]:
    """Return a list of (backend, total_blocks) for the given run_id.

    Aggregates num_blocks across all epochs and pools.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT backend, SUM(num_blocks) AS total_blocks
        FROM blocks
        WHERE run_id = ?
        GROUP BY backend
        ORDER BY total_blocks DESC;
        """,
        (run_id,),
    )
    rows = cur.fetchall()
    if not rows:
        err = f"No block data found in 'blocks' table for run_id={run_id}."
        raise RuntimeError(err)
    return rows


def plot_backend_blocks(
    backend_data: list[tuple[str, int]], *, run_name: str, output_path: pl.Path
) -> None:
    """Plot a bar chart of total blocks per backend."""
    backends = [b for b, _ in backend_data]
    totals = [t for _, t in backend_data]
    df = pd.DataFrame({"backend": backends, "total_blocks": totals})

    sns.set_theme(style="whitegrid")

    plt.figure(figsize=(8, 5))
    ax = sns.barplot(data=df, x="backend", y="total_blocks")

    ax.set_xlabel("Backend")
    ax.set_ylabel("Total blocks over run")
    ax.set_title(f"Total blocks per backend in run {run_name}")

    # Annotate bars with values (type-narrow containers to BarContainer)
    for c in ax.containers:
        if isinstance(c, mcontainer.BarContainer):
            ax.bar_label(c)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a bar chart of total blocks per backend for the latest run "
            "from an SQLite database."
        )
    )
    parser.add_argument(
        "-d",
        "--dbpath",
        required=True,
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "-n",
        "--name",
        required=True,
        help="Name of the run (for labeling purposes).",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output image filename.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dbpath = pl.Path(args.dbpath)
    output_path = pl.Path(args.output)

    if not dbpath.exists():
        print(f"Error: database file '{args.dbpath}' does not exist.", file=sys.stderr)
        return 1

    try:
        with contextlib.closing(sqlite3.connect(dbpath)) as conn, conn:
            run_id = get_latest_run_id(conn)
            backend_data = get_blocks_per_backend(conn, run_id=run_id)
            plot_backend_blocks(
                backend_data=backend_data, run_name=args.name, output_path=output_path
            )
    except (sqlite3.Error, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Saved graph to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
