import ast
import json
import os

import pandas as pd
from pathlib import Path
import argparse

from aws_db_utils import get_identifier_last_run_from_table, get_column_names_from_table, \
    add_column_to_table, add_bulk_values_into_db, add_single_value_into_db

RESULTS_FILE_NAME = r"sync_results.json"


def main():
    env = vars(args)["environment"]
    if "-" in env:
        env = f"`{env}`"
    print(f" !!! env: {env}")

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    print(f"  ==== Read the test results file - {current_directory / RESULTS_FILE_NAME}")
    with open(RESULTS_FILE_NAME, "r") as json_file:
        sync_test_results_dict = json.load(json_file)

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - listdir: {os.listdir(current_directory)}")

    print("  ==== Move to 'sync_tests' directory")
    os.chdir(current_directory / "sync_tests")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    print("  ==== Check if there are DB columns for all the eras")
    print(f"Get the list of the existing eras in test")
    eras_in_test = sync_test_results_dict["eras_in_test"].replace("[", "").replace("]", "").replace(
        '"', '').split(", ")
    print(f"eras_in_test: {eras_in_test}")

    print(f"Get the column names inside the {env} DB tables")
    table_column_names = get_column_names_from_table(env)
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
                    add_column_to_table(env, column_name, "VARCHAR(255)")

    sync_test_results_dict["identifier"] = sync_test_results_dict["env"] + "_" + str(
        int(get_identifier_last_run_from_table(env).split("_")[-1]) + 1)
    print("=======================================")
    print(f"======= identifier: {sync_test_results_dict['identifier']} =======")
    print("=======================================")

    print(f"  ==== Write test values into the {env} DB table")
    test_results_dict = {i: sync_test_results_dict[i] for i in sync_test_results_dict if i not in ["sync_duration_per_epoch", "log_values"]}

    col_to_insert = list(test_results_dict.keys())
    val_to_insert = list(test_results_dict.values())
    if not add_single_value_into_db(env, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)

    if env == "mainnet":
        print(f"  ==== Write test values into the {env + '_logs'} DB table")
        log_values_dict = ast.literal_eval(str((sync_test_results_dict["log_values"])))

        df1_column_names = ["identifier", "timestamp", "slot_no", "ram_bytes", "cpu_percent"]
        df1 = pd.DataFrame(columns=df1_column_names)

        print(f"    ==== Creating the dataframe with the test values")
        for key, val in log_values_dict.items():
            new_row_data = {"identifier": sync_test_results_dict["identifier"],
                       "timestamp": key,
                       "slot_no": val["tip"],
                       "ram_bytes": val["ram"],
                       "cpu_percent": val["cpu"]}
            
            new_row = pd.DataFrame([new_row_data])
            df1 = pd.concat([df1, new_row], ignore_index=True)
            
        col_to_insert = list(df1.columns)
        val_to_insert = df1.values.tolist()
        if not add_bulk_values_into_db(env + '_logs', col_to_insert, val_to_insert):
            print(f"col_to_insert: {col_to_insert}")
            print(f"val_to_insert: {val_to_insert}")
            exit(1)

        print(f"  ==== Write test values into the {env + '_epoch_duration'} DB table")
        sync_duration_values_dict = ast.literal_eval(
            str(sync_test_results_dict["sync_duration_per_epoch"]))
        epoch_list = list(sync_duration_values_dict.keys())

        df2_column_names = ["identifier", "epoch_no", "sync_duration_secs"]
        df2 = pd.DataFrame(columns=df2_column_names)

        # ignoring the current/last epoch that is not synced completely
        for epoch in epoch_list[:-1]:
            new_row = {"identifier": sync_test_results_dict["identifier"],
                       "epoch_no": epoch,
                       "sync_duration_secs": sync_duration_values_dict[epoch]}
            row_df = pd.DataFrame([new_row])
            df2 = pd.concat([row_df, df2], ignore_index=True)

        col_to_insert = list(df2.columns)
        val_to_insert = df2.values.tolist()
        if not add_bulk_values_into_db(env + '_epoch_duration', col_to_insert, val_to_insert):
            print(f"col_to_insert: {col_to_insert}")
            print(f"val_to_insert: {val_to_insert}")
            exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add sync test values into database\n\n")

    parser.add_argument("-e", "--environment",
                        help="The environment on which to run the tests - shelley-qa, testnet, staging, mainnet, preprod, preview")

    args = parser.parse_args()

    main()
