import sqlite3
from sqlite3 import Error

DATABASE_NAME = r"./automated_tests_results.db"


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
    node_nightly_table = """ CREATE TABLE IF NOT EXISTS node_nightly (
                                        build_id text NOT NULL,
                                        build_no integer NOT NULL,
                                        build_started_at text NOT NULL,
                                        build_finished_at text NOT NULL,
                                        build_duration text NOT NULL,
                                        build_status text NOT NULL,
                                        build_web_url text NOT NULL,
                                        test_branch text,                                    
                                        node_branch text,
                                        node_rev text,
                                        cluster_era text NOT NULL,                                        
                                        tx_era text NOT NULL 
                                    ); """

    dbsync_nightly_table = """ CREATE TABLE IF NOT EXISTS dbsync_nightly (
                                        build_id text NOT NULL,
                                        build_no integer NOT NULL,
                                        build_started_at text NOT NULL,
                                        build_finished_at text NOT NULL,
                                        build_duration text NOT NULL,
                                        build_status text NOT NULL,
                                        build_web_url text NOT NULL,
                                        test_branch text NOT NULL, 
                                        node_branch text NOT NULL,
                                        node_rev text,   
                                        dbsync_branch text,                                        
                                        dbsync_rev text,
                                        cluster_era text NOT NULL,                                        
                                        tx_era text NOT NULL                                     
                                    ); """

    node_cli_table = """ CREATE TABLE IF NOT EXISTS node_cli (
                                        build_id text NOT NULL,
                                        build_no integer NOT NULL,
                                        build_started_at text NOT NULL,
                                        build_finished_at text NOT NULL,
                                        build_duration text NOT NULL,
                                        build_status text NOT NULL,
                                        build_web_url text NOT NULL,
                                        test_branch text NOT NULL, 
                                        node_branch text NOT NULL,
                                        node_rev text NOT NULL,     
                                        cluster_era text NOT NULL,                                        
                                        tx_era text NOT NULL 
                                    ); """

    dbsync_cli_table = """ CREATE TABLE IF NOT EXISTS dbsync_cli (
                                        build_id text NOT NULL,
                                        build_no integer NOT NULL,
                                        build_started_at text NOT NULL,
                                        build_finished_at text NOT NULL,
                                        build_duration text NOT NULL,
                                        build_status text NOT NULL,
                                        build_web_url text NOT NULL,
                                        test_branch text NOT NULL, 
                                        node_branch text NOT NULL,
                                        node_rev text NOT NULL,   
                                        dbsync_branch text NOT NULL,                                        
                                        dbsync_rev text NOT NULL,                                        
                                        cluster_era text NOT NULL,                                        
                                        tx_era text NOT NULL 
                                    ); """

    # create a database connection
    conn = create_connection(DATABASE_NAME)

    create_tables_list = [
        node_nightly_table,
        dbsync_nightly_table,
        node_cli_table,
        dbsync_cli_table,
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
