import ast
import json
import os
import sqlite3
from collections import OrderedDict
from sqlite3 import Error
from pathlib import Path
import argparse
import pandas as pd

DATABASE_NAME = r"node_sync_tests_results.db"
RESULTS_FILE_NAME = r"sync_results.json"


def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except Error as e:
        print(f"!!! Error connecting to the database:\n {e}")

    return conn


def add_test_values_into_db(table_name, col_names_list, col_values_list):
    current_directory = Path.cwd()
    database_path = Path(current_directory) / DATABASE_NAME
    print(f"  -- database_path: {database_path}")

    col_names = ','.join(col_names_list)
    col_spaces = ','.join(['?'] * len(col_names_list))
    conn = create_connection(database_path)
    sql_query = f"INSERT INTO {table_name} (%s) values(%s)" % (col_names, col_spaces)
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query, col_values_list)
        conn.commit()
        cur.close()
    except sqlite3.Error as error:
        print(f"  -- !!! ERROR: Failed to insert data into {table_name} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()
    return True


def export_db_table_to_csv(table_name):
    print(f"Exporting {table_name} table into CSV file")
    current_directory = Path.cwd()

    database_path = Path(current_directory) / DATABASE_NAME
    csv_files_path = Path(current_directory) / "csv_files"

    print(f"  -- database_path : {database_path}")
    print(f"  -- csv_files_path: {csv_files_path}")

    Path(csv_files_path).mkdir(parents=True, exist_ok=True)

    conn = create_connection(database_path)
    sql_query = f"select * from {table_name}"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)

        with open(csv_files_path / f"{table_name}.csv", "w") as csv_file:
            df = pd.read_sql(f"select * from {table_name}", conn)
            df.to_csv(csv_file, escapechar="\n", index=False)

        conn.commit()
        cur.close()

        print(f"  -- Data exported Successfully into {csv_files_path / f'{table_name}.csv'}")
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to export {table_name} table to CSV:\n", error)
        return False
    finally:
        if conn:
            conn.close()
    return True


def get_column_names_from_table(env):
    table_name = env
    print(f"Getting the column names from {table_name} table")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    database_path = Path(current_directory) / DATABASE_NAME
    print(f"  -- database_path: {database_path}")
    conn = create_connection(database_path)
    sql_query = f"select * from {table_name}"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        col_name_list = [res[0] for res in cur.description]
        return col_name_list
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to get column names from {table_name} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()


def get_last_row_no(table_name):
    print(f"Getting the last row no from {table_name} table")
    current_directory = Path.cwd()
    print(f"  -- current_directory: {current_directory}")

    database_path = Path(current_directory) / DATABASE_NAME
    print(f"  -- database_path: {database_path}")
    conn = create_connection(database_path)

    sql_query = f"SELECT count(*) FROM {table_name};"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        last_row_no = cur.fetchone()[0]
        return last_row_no
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to get the last row no from {table_name} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()


def add_column_to_table(env, column_name, column_type):
    table_name = env
    print(f"Adding column {column_name} with type {column_type} to {table_name} table")
    current_directory = Path.cwd()

    database_path = Path(current_directory) / DATABASE_NAME
    print(f"  -- database_path: {database_path}")
    conn = create_connection(database_path)
    sql_query = f"alter table {table_name} add column {column_name} {column_type}"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        col_name_list = [res[0] for res in cur.description]
        return col_name_list
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to add {column_name} column into {table_name} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()


def main():
    env = vars(args)["environment"]

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    print(f"  ==== Read the test results file - {current_directory / RESULTS_FILE_NAME}")
    with open(RESULTS_FILE_NAME, "r") as json_file:
        sync_test_results_dict = json.load(json_file)

    print("Print all test results (key/values)")
    for key in sync_test_results_dict:
        print(f"{key}: {sync_test_results_dict[key]}")

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - sync_tests listdir: {os.listdir(current_directory)}")

    print("  ==== Move to 'sync_tests' directory")
    os.chdir(current_directory / "sync_tests")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - sync_tests listdir: {os.listdir(current_directory)}")

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
        if len(era_columns) == 0:
            print(f" === Adding columns for {era} era into the the {env} table")
            new_columns_list = [str(era + "_start_time"),
                                str(era + "_start_epoch"),
                                str(era + "_slots_in_era"),
                                str(era + "_start_sync_time"),
                                str(era + "_end_sync_time"),
                                str(era + "_sync_duration_secs")]
            for column_name in new_columns_list:
                add_column_to_table(env, column_name, "TEXT")

    sync_test_results_dict["identifier"] = sync_test_results_dict["env"] + "_" + str(get_last_row_no(env))

    print(f"  ==== Write test values into the {env + '_logs_table'} DB table")
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
        if not add_test_values_into_db(env + "_logs", col_list2, col_values2):
            print(f"col_list2  : {col_list2}")
            print(f"col_values2: {col_values2}")
            exit(1)

    print(f"  ==== Write test values into the {env + '_epoch_duration_table'} DB table")
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
        if not add_test_values_into_db(env + "_epoch_duration", col_list3, col_values3):
            print(f"col_list3  : {col_list3}")
            print(f"col_values3: {col_values3}")
            exit(1)

    print(f"  ==== Write test values into the {env} DB table")
    del sync_test_results_dict["sync_duration_per_epoch"]
    del sync_test_results_dict["log_values"]

    col_list = list(sync_test_results_dict.keys())
    col_values = list(sync_test_results_dict.values())
    if not add_test_values_into_db(env, col_list, col_values):
        print(f"col_list  : {col_list}")
        print(f"col_values: {col_values}")
        exit(1)

    print(f"  ==== Exporting the {env} table as CSV")
    export_db_table_to_csv(env)

    print(f"  ==== Exporting the {env + '_logs'} table as CSV")
    export_db_table_to_csv(env + '_logs')

    print(f"  ==== Exporting the {env + '_epoch_duration'} table as CSV")
    export_db_table_to_csv(env + '_epoch_duration')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add sync test values into database\n\n")

    parser.add_argument("-e", "--environment",
                        help="The environment on which to run the tests - shelley_qa, testnet, staging or mainnet.")

    args = parser.parse_args()

    main()