import ast
import json
import os
import time
from collections import OrderedDict
from pathlib import Path
import argparse

from sqlite_utils import get_column_names_from_table, add_column_to_table, get_last_row_no, \
    add_test_values_into_db, export_db_table_to_csv, drop_table
from node_create_db import create_table, mainnet_tx_count_table

DATABASE_NAME = r"node_sync_tests_results.db"
RESULTS_FILE_NAME = r"sync_results.json"


def main():
    env = vars(args)["environment"]

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    print(f"  ==== Read the test results file - {current_directory / RESULTS_FILE_NAME}")
    with open(RESULTS_FILE_NAME, "r") as json_file:
        sync_test_results_dict = json.load(json_file)

    # print(f"sync_test_results_dict: {sync_test_results_dict}")
    # for key in sync_test_results_dict:
    #     print(f"{key}: {sync_test_results_dict[key]}")

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - listdir: {os.listdir(current_directory)}")

    print("  ==== Move to 'sync_tests' directory")
    os.chdir(current_directory / "sync_tests")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - sync_tests listdir: {os.listdir(current_directory)}")
    database_path = Path(current_directory) / DATABASE_NAME
    print(f"database_path: {database_path}")

    print("  ==== Check if there are DB columns for all the eras")

    print(f"Get the list of the existing eras in test")
    eras_in_test = sync_test_results_dict["eras_in_test"].replace("[", "").replace("]", "").replace(
        '"', '').split(", ")
    print(f"eras_in_test: {eras_in_test}")

    print(f"Get the column names inside the {env} DB tables")
    table_column_names = get_column_names_from_table(database_path, env)
    print(f"  -- table_column_names: {table_column_names}")

    for era in eras_in_test:
        era_columns = [i for i in table_column_names if i.startswith(era)]
        if len(era_columns) != 7:
            print(f" === Adding columns for {era} era into the the {env} table")
            new_columns_list = [str(era + "_start_time"),
                                str(era + "_start_epoch"),
                                str(era + "_slots_in_era"),
                                str(era + "_start_sync_time"),
                                str(era + "_end_sync_time"),
                                str(era + "_sync_duration_secs"),
                                str(era + "_sync_speed_sps")]
            for column_name in new_columns_list:
                if column_name not in table_column_names:
                    add_column_to_table(database_path, env, column_name, "TEXT")

    sync_test_results_dict["identifier"] = sync_test_results_dict["env"] + "_" + str(get_last_row_no(database_path, env))

    time.sleep(5)

    print(f"  ==== Write test values into the {env + '_logs'} DB table")
    log_values_json = ast.literal_eval(str((sync_test_results_dict["log_values"])))
    timestamp_list = list(log_values_json.keys())
    for timestamp1 in timestamp_list:
        line_dict = OrderedDict()
        line_dict["identifier"] = sync_test_results_dict["identifier"]
        line_dict["timestamp"] = timestamp1
        line_dict["slot_no"] = log_values_json[timestamp1]["tip"]
        line_dict["ram_bytes"] = log_values_json[timestamp1]["ram"]
        line_dict["cpu_percent"] = log_values_json[timestamp1]["cpu"]

        col_list2 = list(line_dict.keys())
        col_values2 = list(line_dict.values())
        if not add_test_values_into_db(database_path, env + "_logs", col_list2, col_values2):
            print(f"col_list2  : {col_list2}")
            print(f"col_values2: {col_values2}")
            exit(1)

    print(f"  ==== Write test values into the {env + '_epoch_duration'} DB table")
    sync_duration_values_json = ast.literal_eval(str(sync_test_results_dict["sync_duration_per_epoch"]))
    epoch_list = list(sync_duration_values_json.keys())
    print(f"epoch_list: {epoch_list}")
    for epoch in epoch_list[:-1]:
        # ignoring the current/last epoch that is not synced completely
        sync_duration_per_epoch_dict = OrderedDict()
        sync_duration_per_epoch_dict["identifier"] = sync_test_results_dict["identifier"]
        sync_duration_per_epoch_dict["epoch_no"] = epoch
        sync_duration_per_epoch_dict["sync_duration_secs"] = sync_duration_values_json[epoch]

        col_list3 = list(sync_duration_per_epoch_dict.keys())
        col_values3 = list(sync_duration_per_epoch_dict.values())
        if not add_test_values_into_db(database_path, env + "_epoch_duration", col_list3, col_values3):
            print(f"col_list3  : {col_list3}")
            print(f"col_values3: {col_values3}")
            exit(1)

    print(f"  ==== Write test values into the {env} DB table")
    del sync_test_results_dict["sync_duration_per_epoch"]
    del sync_test_results_dict["log_values"]

    col_list = list(sync_test_results_dict.keys())
    col_values = list(sync_test_results_dict.values())
    if not add_test_values_into_db(database_path, env, col_list, col_values):
        print(f"col_list  : {col_list}")
        print(f"col_values: {col_values}")
        exit(1)

    print(f"  ==== Exporting the {env} table as CSV")
    export_db_table_to_csv(database_path, env)

    print(f"  ==== Exporting the {env + '_logs'} table as CSV")
    export_db_table_to_csv(database_path, env + '_logs')

    print(f"  ==== Exporting the {env + '_epoch_duration'} table as CSV")
    export_db_table_to_csv(database_path, env + '_epoch_duration')


def update_mainnet_tx_count_per_epoch(actual_epoch_no):
    from sync_tests.node_sync_test import get_tx_count_per_epoch
    current_directory = Path.cwd()
    database_path = Path(current_directory) / DATABASE_NAME
    table_name = "mainnet_tx_count"
    drop_table(database_path, table_name)
    create_table(database_path, mainnet_tx_count_table)

    tx_count_dict = {}
    for epoch_no in range(actual_epoch_no):
        tx_count = get_tx_count_per_epoch("mainnet", epoch_no)
        tx_count_dict["epoch_no"] = epoch_no
        tx_count_dict["tx_count"] = tx_count
        print(f"epoch: {epoch_no}: {tx_count}")

        col_list = list(tx_count_dict.keys())
        col_values = list(tx_count_dict.values())

        if not add_test_values_into_db(database_path, table_name, col_list, col_values):
            print(f"col_list  : {col_list}")
            print(f"col_values: {col_values}")
            exit(1)

    print(f"  ==== Exporting the {table_name} table as CSV")
    export_db_table_to_csv(database_path, table_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add sync test values into database\n\n")

    parser.add_argument("-e", "--environment",
                        help="The environment on which to run the tests - shelley_qa, testnet, staging or mainnet.")

    args = parser.parse_args()

    main()
    # update_mainnet_tx_count_per_epoch(307)