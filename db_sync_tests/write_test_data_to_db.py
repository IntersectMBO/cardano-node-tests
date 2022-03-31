import json
import os

from pathlib import Path
import argparse

from aws_db_utils import get_identifier_last_run_from_table, get_column_names_from_table, \
    add_column_to_table, add_bulk_rows_into_db, add_single_row_into_db, create_table


TEST_RESULTS_FILE_NAME = 'test_results.json'
EPOCH_SYNC_TIMES_FILE_NAME = 'epoch_sync_times_dump.json'
DB_SYNC_PERF_STATS_FILE_NAME = "db_sync_performance_stats.json"


def main():

    env = vars(args)["environment"]

    os.chdir(Path.cwd() / 'cardano-db-sync')
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - listdir: {os.listdir(current_directory)}")

    print(f"  ==== Read summary test results from {TEST_RESULTS_FILE_NAME}")
    with open(TEST_RESULTS_FILE_NAME, "r") as json_file:
        sync_test_results_dict = json.load(json_file)

    test_summary_table = env + '_db_sync'
    test_id = str(int(get_identifier_last_run_from_table(test_summary_table).split("_")[-1]) + 1)
    identifier = env + "_" + test_id
    sync_test_results_dict["identifier"] = identifier

    print(f"  ==== Write test values into the {test_summary_table} DB table:")
    col_to_insert = list(sync_test_results_dict.keys())
    val_to_insert = list(sync_test_results_dict.values())

    if not add_single_row_into_db(test_summary_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)

    print(f"  ==== Read epoch sync duration times from {EPOCH_SYNC_TIMES_FILE_NAME}")
    with open(EPOCH_SYNC_TIMES_FILE_NAME, "r") as json_db_dump_file:
        epoch_sync_times = json.load(json_db_dump_file)

    epoch_duration_table = env + '_epoch_duration_db_sync'
    print(f"  ==== Write test values into the {epoch_duration_table} DB table:")
    col_to_insert = ['identifier', 'epoch_no', 'sync_duration_secs']
    val_to_insert = [ (identifier, e['no'], e['seconds']) for e in epoch_sync_times ]

    if not add_bulk_rows_into_db(epoch_duration_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)

    print(f"  ==== Read performance stats from {DB_SYNC_PERF_STATS_FILE_NAME}")
    with open(DB_SYNC_PERF_STATS_FILE_NAME, "r") as json_perf_stats_file:
        db_sync_performance_stats = json.load(json_perf_stats_file)

    db_sync_performance_stats_table = env + '_performance_stats_db_sync'
    print(f"  ==== Write test values into the {db_sync_performance_stats_table} DB table:")
    col_to_insert = ['identifier', 'time', 'slot_no', 'cpu_percent_usage', 'rss_mem_usage']
    val_to_insert = [ (identifier, e['time'], e['slot_no'], e['cpu_percent_usage'], e['rss_mem_usage']) for e in db_sync_performance_stats ]

    if not add_bulk_rows_into_db(db_sync_performance_stats_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add sync test values into database\n\n")

    parser.add_argument("-e", "--environment",
                        help="The environment on which to run the tests - shelley_qa, testnet, staging or mainnet.")

    args = parser.parse_args()

    main()
