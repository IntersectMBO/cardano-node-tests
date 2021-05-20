import sqlite3
from sqlite3 import Error

DATABASE_NAME = r"./sync_tests_results.db"


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
    shelley_qa_table = """ CREATE TABLE IF NOT EXISTS shelley_qa (
                                        identifier text NOT NULL PRIMARY KEY,
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        cli_version1 text NOT NULL,
                                        cli_version2 text,
                                        cli_git_rev1 text NOT NULL,
                                        cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        last_slot_no1 text NOT NULL,
                                        last_slot_no2 text,
                                        start_node_secs1 text NOT NULL,
                                        start_node_secs2 text,     
                                        sync_time_seconds1 integer,  
                                        sync_time1 text,  
                                        sync_time_seconds2 integer,  
                                        sync_time2 text,                                                                                                                   
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        chain_size_bytes text NOT NULL,
                                        eras_in_test,
                                        no_of_cpu_cores,
                                        total_ram_in_GB,
                                        epoch_no_d_zero,
                                        start_slot_no_d_zero,
                                        byron_start_time text,
                                        byron_start_epoch integer,
                                        byron_slots_in_era integer,
                                        byron_start_sync_time text,
                                        byron_end_sync_time text,
                                        byron_sync_duration_secs integer,
                                        byron_sync_speed_sps integer,
                                        shelley_start_time text,
                                        shelley_start_epoch integer,
                                        shelley_slots_in_era integer,
                                        shelley_start_sync_time text,
                                        shelley_end_sync_time text,
                                        shelley_sync_duration_secs integer,
                                        shelley_sync_speed_sps integer,
                                        allegra_start_time text,
                                        allegra_start_epoch integer,
                                        allegra_slots_in_era integer,
                                        allegra_start_sync_time text,
                                        allegra_end_sync_time text,
                                        allegra_sync_duration_secs integer,
                                        allegra_sync_speed_sps integer,
                                        mary_start_time text,
                                        mary_start_epoch integer,
                                        mary_slots_in_era integer,
                                        mary_start_sync_time text,
                                        mary_end_sync_time text,
                                        mary_sync_duration_secs integer,
                                        mary_sync_speed_sps integer
                                    ); """

    testnet_logs_table = """ CREATE TABLE IF NOT EXISTS testnet_logs (
                                        identifier text NOT NULL,
                                        timestamp text,
                                        slot_no integer,
                                        ram_bytes text,
                                        cpu_percent text
                                    ); """

    testnet_epoch_duration_table = """ CREATE TABLE IF NOT EXISTS testnet_epoch_duration (
                                        identifier text NOT NULL,
                                        epoch_no integer,
                                        sync_duration_secs integer
                                    ); """

    testnet_table = """ CREATE TABLE IF NOT EXISTS testnet (
                                        identifier text NOT NULL PRIMARY KEY,
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        cli_version1 text NOT NULL,
                                        cli_version2 text,
                                        cli_git_rev1 text NOT NULL,
                                        cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        last_slot_no1 text NOT NULL,
                                        last_slot_no2 text,
                                        start_node_secs1 text NOT NULL,
                                        start_node_secs2 text,     
                                        sync_time_seconds1 integer,  
                                        sync_time1 text,  
                                        sync_time_seconds2 integer,  
                                        sync_time2 text,                                                                           
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        chain_size_bytes text NOT NULL,
                                        eras_in_test,
                                        no_of_cpu_cores,
                                        total_ram_in_GB,
                                        epoch_no_d_zero,
                                        start_slot_no_d_zero,
                                        byron_start_time text,
                                        byron_start_epoch integer,
                                        byron_slots_in_era integer,
                                        byron_start_sync_time text,
                                        byron_end_sync_time text,
                                        byron_sync_duration_secs integer,
                                        byron_sync_speed_sps integer,
                                        shelley_start_time text,
                                        shelley_start_epoch integer,
                                        shelley_slots_in_era integer,
                                        shelley_start_sync_time text,
                                        shelley_end_sync_time text,
                                        shelley_sync_duration_secs integer,
                                        shelley_sync_speed_sps integer,
                                        allegra_start_time text,
                                        allegra_start_epoch integer,
                                        allegra_slots_in_era integer,
                                        allegra_start_sync_time text,
                                        allegra_end_sync_time text,
                                        allegra_sync_duration_secs integer,
                                        allegra_sync_speed_sps integer,
                                        mary_start_time text,
                                        mary_start_epoch integer,
                                        mary_slots_in_era integer,
                                        mary_start_sync_time text,
                                        mary_end_sync_time text,
                                        mary_sync_duration_secs integer,
                                        mary_sync_speed_sps integer
                                    ); """

    shelley_qa_logs_table = """ CREATE TABLE IF NOT EXISTS shelley_qa_logs (
                                        identifier text NOT NULL,
                                        timestamp text,
                                        slot_no integer,
                                        ram_bytes text,
                                        cpu_percent text
                                    ); """

    shelley_qa_epoch_duration_table = """ CREATE TABLE IF NOT EXISTS shelley_qa_epoch_duration (
                                        identifier text NOT NULL,
                                        epoch_no integer,
                                        sync_duration_secs integer
                                    ); """

    staging_table = """ CREATE TABLE IF NOT EXISTS staging (
                                        identifier text NOT NULL PRIMARY KEY,
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        cli_version1 text NOT NULL,
                                        cli_version2 text,
                                        cli_git_rev1 text NOT NULL,
                                        cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        last_slot_no1 text NOT NULL,
                                        last_slot_no2 text,
                                        start_node_secs1 text NOT NULL,
                                        start_node_secs2 text,    
                                        sync_time_seconds1 integer,  
                                        sync_time1 text,  
                                        sync_time_seconds2 integer,  
                                        sync_time2 text,                                                                            
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        chain_size_bytes text NOT NULL,
                                        eras_in_test,
                                        no_of_cpu_cores,
                                        total_ram_in_GB,
                                        epoch_no_d_zero,
                                        start_slot_no_d_zero,
                                        byron_start_time text,
                                        byron_start_epoch integer,
                                        byron_slots_in_era integer,
                                        byron_start_sync_time text,
                                        byron_end_sync_time text,
                                        byron_sync_duration_secs integer,
                                        byron_sync_speed_sps integer,
                                        shelley_start_time text,
                                        shelley_start_epoch integer,
                                        shelley_slots_in_era integer,
                                        shelley_start_sync_time text,
                                        shelley_end_sync_time text,
                                        shelley_sync_duration_secs integer,
                                        shelley_sync_speed_sps integer,
                                        allegra_start_time text,
                                        allegra_start_epoch integer,
                                        allegra_slots_in_era integer,
                                        allegra_start_sync_time text,
                                        allegra_end_sync_time text,
                                        allegra_sync_duration_secs integer,
                                        allegra_sync_speed_sps integer,
                                        mary_start_time text,
                                        mary_start_epoch integer,
                                        mary_slots_in_era integer,
                                        mary_start_sync_time text,
                                        mary_end_sync_time text,
                                        mary_sync_duration_secs integer,
                                        mary_sync_speed_sps integer
                                    ); """

    staging_logs_table = """ CREATE TABLE IF NOT EXISTS staging_logs (
                                        identifier text NOT NULL,
                                        timestamp text,
                                        slot_no integer,
                                        ram_bytes text,
                                        cpu_percent text
                                    ); """

    staging_epoch_duration_table = """ CREATE TABLE IF NOT EXISTS staging_epoch_duration (
                                        identifier text NOT NULL,
                                        epoch_no integer,
                                        sync_duration_secs integer
                                    ); """

    mainnet_table = """ CREATE TABLE IF NOT EXISTS mainnet (
                                        identifier text NOT NULL PRIMARY KEY,
                                        env text NOT NULL,
                                        tag_no1 text NOT NULL,
                                        tag_no2 text,
                                        cli_version1 text NOT NULL,
                                        cli_version2 text,
                                        cli_git_rev1 text NOT NULL,
                                        cli_git_rev2 text,
                                        start_sync_time1 text NOT NULL,
                                        end_sync_time1 text NOT NULL,
                                        start_sync_time2 text,
                                        end_sync_time2 text,
                                        last_slot_no1 text NOT NULL,
                                        last_slot_no2 text,
                                        start_node_secs1 text NOT NULL,
                                        start_node_secs2 text,    
                                        sync_time_seconds1 integer,  
                                        sync_time1 text,  
                                        sync_time_seconds2 integer,  
                                        sync_time2 text,                                                                            
                                        total_chunks1 integer NOT NULL,
                                        total_chunks2 integer,
                                        platform_system text NOT NULL,
                                        platform_release text NOT NULL,
                                        platform_version text NOT NULL,
                                        chain_size_bytes text NOT NULL,
                                        eras_in_test,
                                        no_of_cpu_cores,
                                        total_ram_in_GB,
                                        epoch_no_d_zero,
                                        start_slot_no_d_zero,
                                        byron_start_time text,
                                        byron_start_epoch integer,
                                        byron_slots_in_era integer,
                                        byron_start_sync_time text,
                                        byron_end_sync_time text,
                                        byron_sync_duration_secs integer,
                                        byron_sync_speed_sps integer,
                                        shelley_start_time text,
                                        shelley_start_epoch integer,
                                        shelley_slots_in_era integer,
                                        shelley_start_sync_time text,
                                        shelley_end_sync_time text,
                                        shelley_sync_duration_secs integer,
                                        shelley_sync_speed_sps integer,
                                        allegra_start_time text,
                                        allegra_start_epoch integer,
                                        allegra_slots_in_era integer,
                                        allegra_start_sync_time text,
                                        allegra_end_sync_time text,
                                        allegra_sync_duration_secs integer,
                                        allegra_sync_speed_sps integer,
                                        mary_start_time text,
                                        mary_start_epoch integer,
                                        mary_slots_in_era integer,
                                        mary_start_sync_time text,
                                        mary_end_sync_time text,
                                        mary_sync_duration_secs integer,
                                        mary_sync_speed_sps integer
                                    ); """

    mainnet_logs_table = """ CREATE TABLE IF NOT EXISTS mainnet_logs (
                                        identifier text NOT NULL,
                                        timestamp text,
                                        slot_no integer,
                                        ram_bytes text,
                                        cpu_percent text
                                    ); """

    mainnet_epoch_duration_table = """ CREATE TABLE IF NOT EXISTS mainnet_epoch_duration (
                                        identifier text NOT NULL,
                                        epoch_no integer,
                                        sync_duration_secs integer
                                    ); """

    # create a database connection
    conn = create_connection(DATABASE_NAME)

    create_tables_list = [
        shelley_qa_table,
        shelley_qa_logs_table,
        shelley_qa_epoch_duration_table,
        testnet_table,
        testnet_logs_table,
        testnet_epoch_duration_table,
        staging_table,
        staging_logs_table,
        staging_epoch_duration_table,
        mainnet_table,
        mainnet_logs_table,
        mainnet_epoch_duration_table,
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
