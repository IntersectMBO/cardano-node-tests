import os
import json

from pathlib import Path
import argparse
import matplotlib.pyplot as plt

from utils import upload_artifact

DB_SYNC_PERF_STATS_FILE_NAME = "db_sync_performance_stats.json"
EPOCH_SYNC_TIMES_FILE_NAME = 'epoch_sync_times_dump.json'
CHART_FILE_NAME = "summary_chart.png"


def main():

    os.chdir(Path.cwd() / 'cardano-db-sync')
    fig = plt.figure(figsize = (14, 10))

    # define epochs sync times chart
    ax_epochs =  fig.add_axes([0.05, 0.05, 0.9, 0.35])
    ax_epochs.set(xlabel='epochs [number]', ylabel='time [min]')
    ax_epochs.set_title('Epochs Sync Times')

    with open(EPOCH_SYNC_TIMES_FILE_NAME, "r") as json_db_dump_file:
        epoch_sync_times = json.load(json_db_dump_file)

    epochs = [ e['no'] for e in epoch_sync_times ]
    epoch_times = [ e['seconds']/60 for e in epoch_sync_times ]
    ax_epochs.bar(epochs, epoch_times)

    # define performance chart
    ax_perf =  fig.add_axes([0.05, 0.5, 0.9, 0.45])
    ax_perf.set(xlabel='time [min]', ylabel='RSS [B]')
    ax_perf.set_title('RSS usage')

    with open(DB_SYNC_PERF_STATS_FILE_NAME, "r") as json_db_dump_file:
        perf_stats = json.load(json_db_dump_file)

    times = [ e['time']/60 for e in perf_stats ]
    rss_mem_usage = [ e['rss_mem_usage'] for e in perf_stats ]

    ax_perf.plot(times, rss_mem_usage)
    fig.savefig(CHART_FILE_NAME)

    upload_artifact(CHART_FILE_NAME)


if __name__ == "__main__":

    main()
