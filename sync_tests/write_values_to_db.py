import sqlite3
from sqlite3 import Error
from pathlib import Path
import argparse
import pandas as pd

database_name = r"sync_results.db"
results_file_name = r"sync_results.log"


def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except Error as e:
        print(f"Error connecting to the database:\n {e}")

    return conn


def add_test_values_into_db(env, test_values):
    print(f"Write values into {env} table")
    current_directory = Path.cwd()
    database_path = Path(current_directory) / "sync_tests" / database_name
    print(f"database_path: {database_path}")

    conn = create_connection(database_path)

    try:
        sql = f' INSERT INTO {env} ' \
              f'(env, tag_no1, tag_no2, pr_no1, pr_no2, cardano_cli_version1, cardano_cli_version2, ' \
              f'cardano_cli_git_rev1, cardano_cli_git_rev2, start_sync_time1, end_sync_time1, start_sync_time2, ' \
              f'end_sync_time2, byron_sync_time_secs1, shelley_sync_time_secs1, shelley_sync_time_secs2, ' \
              f'total_chunks1, total_chunks2, latest_block_no1, latest_block_no2, latest_slot_no1, latest_slot_no2, ' \
              f'start_node_seconds1, start_node_seconds2, platform_system, platform_release, platform_version, ' \
              f'sync_details1) ' \
              f'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'

        print(f"sql: {sql}")

        cur = conn.cursor()
        cur.execute(sql, test_values)
        conn.commit()
        cur.close()
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to insert data into {env} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()
    return True


def export_db_tables_to_csv(env):
    print(f"Export {env} table into CSV file")
    current_directory = Path.cwd()
    database_path = Path(current_directory) / "sync_tests" / database_name
    csv_files_path = Path(current_directory) / "sync_tests" / "csv_files"
    print(f"database_path : {database_path}")
    print(f"csv_files_path: {csv_files_path}")

    Path(csv_files_path).mkdir(parents=True, exist_ok=True)

    conn = create_connection(database_path)
    try:
        sql = f"select * from {env}"
        print(f"sql: {sql}")

        cur = conn.cursor()
        cur.execute(sql)

        with open(csv_files_path / f"{env}.csv", "w") as csv_file:
            df = pd.read_sql(f"select * from {env}", conn)
            df.to_csv(csv_file, escapechar="\n", index=False)

        conn.commit()
        cur.close()

        print(f"Data exported Successfully into {csv_files_path / f'{env}.csv'}")
    except sqlite3.Error as error:
        print(f"!!! ERROR: Failed to insert data into {env} table:\n", error)
        return False
    finally:
        if conn:
            conn.close()
    return True


def main():
    env = vars(args)["environment"]

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    # sync_test_clean_state.py is creating the "sync_results.log" file that has the test values
    # to be added into the db
    with open(current_directory / "sync_tests" / results_file_name, "r+") as file:
        file_values = file.read()
        print(f"file_values: {file_values}")

        test_values = file_values.replace("(", "").replace(")", "").replace("'", "").split(", ", 27)

    print(f"env: {env}")
    print(f"test_values: {test_values}")

    # Add the test values into the local copy of the database (to be pushed into master)
    add_test_values_into_db(env, test_values)

    # Export data into CSV file
    export_db_tables_to_csv(env)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add sync test values into database\n\n")

    parser.add_argument("-e", "--environment",
                        help="The environment on which to run the tests - testnet, staging or mainnet.")

    args = parser.parse_args()

    main()
