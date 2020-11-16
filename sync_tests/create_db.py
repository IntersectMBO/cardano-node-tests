import sqlite3
from sqlite3 import Error

DATABASE_NAME = r"./sync_results.db"


def create_connection(db_file):
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        return conn
    except Error as e:
        print(f"Error connecting to the database:\n {e}")

    return conn


def create_table(conn, create_table_sql):
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(create_table_sql)
    except Error as e:
        print(f"Error creating table:\n {e}")
    finally:
        if cursor:
            cursor.close()


def create_db_tables():
    sql_create_testnet_table = """ CREATE TABLE IF NOT EXISTS testnet (
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        pr_no1 text NOT NULL,
                                        pr_no2 text,
                                        cardano_cli_version1 text NOT NULL,
                                        cardano_cli_version2 text,
                                        cardano_cli_git_rev1 text NOT NULL,
                                        cardano_cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        byron_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs2 text,
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        latest_block_no1 text NOT NULL,
                                        latest_block_no2 text,
                                        latest_slot_no1 text NOT NULL,
                                        latest_slot_no2 text,
                                        start_node_seconds1 text NOT NULL,
                                        start_node_seconds2 text NOT NULL,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        sync_details1 text NOT NULL
                                    ); """

    sql_create_staging_table = """ CREATE TABLE IF NOT EXISTS staging (
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        pr_no1 text NOT NULL,
                                        pr_no2 text,
                                        cardano_cli_version1 text NOT NULL,
                                        cardano_cli_version2 text,
                                        cardano_cli_git_rev1 text NOT NULL,
                                        cardano_cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        byron_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs2 text,
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        latest_block_no1 text NOT NULL,
                                        latest_block_no2 text,
                                        latest_slot_no1 text NOT NULL,
                                        latest_slot_no2 text,
                                        start_node_seconds1 text NOT NULL,
                                        start_node_seconds2 text NOT NULL,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        sync_details1 text NOT NULL
                                    ); """

    sql_create_mainnet_table = """ CREATE TABLE IF NOT EXISTS mainnet (
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        pr_no1 text NOT NULL,
                                        pr_no2 text,
                                        cardano_cli_version1 text NOT NULL,
                                        cardano_cli_version2 text,
                                        cardano_cli_git_rev1 text NOT NULL,
                                        cardano_cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        byron_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs1 text NOT NULL,
                                        shelley_sync_time_secs2 text,
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        latest_block_no1 text NOT NULL,
                                        latest_block_no2 text,
                                        latest_slot_no1 text NOT NULL,
                                        latest_slot_no2 text,
                                        start_node_seconds1 text NOT NULL,
                                        start_node_seconds2 text NOT NULL,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        sync_details1 text NOT NULL
                                    ); """

    # create a database connection
    conn = create_connection(DATABASE_NAME)

    create_tables_list = [
        sql_create_testnet_table,
        sql_create_staging_table,
        sql_create_mainnet_table,
    ]

    # create tables
    if conn is not None:
        for table in create_tables_list:
            create_table(conn, table)
    else:
        print("Error! cannot create the database connection.")

    conn.close()


if __name__ == "__main__":
    create_db_tables()
