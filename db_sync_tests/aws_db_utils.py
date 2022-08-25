import os

import pymysql.cursors
import pandas as pd


def create_connection():
    conn = None
    try:
        conn = pymysql.connect(host=os.environ["AWS_DB_HOSTNAME"],
                               user=os.environ["AWS_DB_USERNAME"],
                               password=os.environ["AWS_DB_PASS"],
                               db=os.environ["AWS_DB_NAME"],
                               )
        return conn
    except Exception as e:
        print(f"!!! Database connection failed due to: {e}")

    return conn


def create_table(table_sql_query):
    conn = create_connection()
    try:
        cur = conn.cursor()
        cur.execute(table_sql_query)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"!!! ERROR: Failed to create table: {e}")
        return False
    finally:
        if conn:
            conn.close()


def drop_table(table_name):
    conn = create_connection()
    sql_query = f"DROP TABLE `{table_name}`;"
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"!!! ERROR: Failed to drop table {table_name}: {e}")
        return False
    finally:
        if conn:
            conn.close()


def get_column_names_from_table(table_name):
    print(f"Getting the column names from table: {table_name}")

    conn = create_connection()
    sql_query = f"select * from `{table_name}`"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        col_name_list = [res[0] for res in cur.description]
        return col_name_list
    except Exception as e:
        print(f"!!! ERROR: Failed to get column names from table: {table_name}: {e}")
        return False
    finally:
        if conn:
            conn.close()


def add_column_to_table(table_name, column_name, column_type):
    print(f"Adding column {column_name} with type {column_type} to {table_name} table")

    conn = create_connection()
    sql_query = f"alter table `{table_name}` add column {column_name} {column_type}"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
    except Exception as e:
        print(f"!!! ERROR: Failed to add {column_name} column into table {table_name} --> {e}")
        return False
    finally:
        if conn:
            conn.close()


def add_single_row_into_db(table_name, col_names_list, col_values_list):
    print(f"Adding 1 new entry into {table_name} table")
    initial_rows_no = get_last_row_no(table_name)
    col_names = ','.join(col_names_list)
    col_spaces = ','.join(['%s'] * len(col_names_list))
    conn = create_connection()
    sql_query = f"INSERT INTO `{table_name}` (%s) values(%s)" % (col_names, col_spaces)
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query, col_values_list)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"  -- !!! ERROR: Failed to insert data into {table_name} table: {e}")
        return False
    finally:
        if conn:
            conn.close()
    final_rows_no = get_last_row_no(table_name)
    print(f"Successfully added {final_rows_no - initial_rows_no} rows into table {table_name}")
    return True


def add_bulk_rows_into_db(table_name, col_names_list, col_values_list):
    print(f"Adding {len(col_values_list)} entries into {table_name} table")
    initial_rows_no = get_last_row_no(table_name)
    col_names = ','.join(col_names_list)
    col_spaces = ','.join(['%s'] * len(col_names_list))
    conn = create_connection()
    sql_query = f"INSERT INTO `{table_name}` (%s) values (%s)" % (col_names, col_spaces)
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.executemany(sql_query, col_values_list)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"  -- !!! ERROR: Failed to bulk insert data into {table_name} table: {e}")
        return False
    finally:
        if conn:
            conn.close()
    final_rows_no = get_last_row_no(table_name)
    print(f"Successfully added {final_rows_no - initial_rows_no} rows into table {table_name}")
    return True


def get_last_row_no(table_name):
    print(f"Getting the no of rows from table: {table_name}")

    conn = create_connection()
    sql_query = f"SELECT count(*) FROM `{table_name}`;"
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        last_row_no = cur.fetchone()[0]
        return last_row_no
    except Exception as e:
        print(f"!!! ERROR: Failed to get the no of rows from table {table_name} --> {e}")
        return False
    finally:
        if conn:
            conn.close()


def get_identifier_last_run_from_table(table_name):
    print(f"Getting the Identifier value of the last run from table {table_name}")

    if get_last_row_no(table_name) == 0:
        return table_name + "_0"
    else:
        conn = create_connection()
        sql_query = f"SELECT identifier FROM `{table_name}` " \
                    f"ORDER BY LPAD(LOWER(identifier), 500,0) DESC LIMIT 1;"
        print(f"  -- sql_query: {sql_query}")
        try:
            cur = conn.cursor()
            cur.execute(sql_query)
            last_identifier = cur.fetchone()[0]
            return last_identifier
        except Exception as e:
            print(f"!!! ERROR: Failed to get the no of rows from table {table_name} --> {e}")
            return False
        finally:
            if conn:
                conn.close()


def get_last_epoch_no_from_table(table_name):
    print(f"Getting the last epoch no value from table {table_name}")

    if get_last_row_no(table_name) == 0:
        return 0
    else:
        conn = create_connection()
        sql_query = f"SELECT MAX(epoch_no) FROM `{table_name}`;;"
        print(f"  -- sql_query: {sql_query}")
        try:
            cur = conn.cursor()
            cur.execute(sql_query)
            last_identifier = cur.fetchone()[0]
            return last_identifier
        except Exception as e:
            print(f"!!! ERROR: Failed to get last epoch no from table {table_name} --> {e}")
            return False
        finally:
            if conn:
                conn.close()


def get_column_values(table_name, column_name):
    print(f"Getting {column_name} column values from table {table_name}")

    conn = create_connection()
    sql_query = f"SELECT {column_name} FROM `{table_name}`;"
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        return [el[0] for el in cur.fetchall()]
    except Exception as e:
        print(f"!!! ERROR: Failed to get {column_name} column values from table {table_name} --> {e}")
        return False
    finally:
        if conn:
            conn.close()


def delete_all_rows_from_table(table_name):
    print(f"Deleting all entries from table: {table_name}")
    conn = create_connection()
    sql_query = f"TRUNCATE TABLE `{table_name}`"
    print(f"  -- sql_query: {sql_query}")
    initial_rows_no = get_last_row_no(table_name)
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"!!! ERROR: Failed to delete all records from table {table_name} --> {e}")
        return False
    finally:
        if conn:
            conn.close()
    final_rows_no = get_last_row_no(table_name)
    print(f"Successfully deleted {initial_rows_no - final_rows_no} rows from table {table_name}")


def delete_record(table_name, column_name, delete_value):
    print(f"Deleting rows containing '{delete_value}' value inside the '{column_name}' column")
    initial_rows_no = get_last_row_no(table_name)
    print(f"Deleting {column_name} = {delete_value} from {table_name} table")

    conn = create_connection()
    sql_query = f"DELETE from `{table_name}` where {column_name}=\"{delete_value}\""
    print(f"  -- sql_query: {sql_query}")
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"!!! ERROR: Failed to delete record {column_name} = {delete_value} from {table_name} table: --> {e}")
        return False
    finally:
        if conn:
            conn.close()
    final_rows_no = get_last_row_no(table_name)
    print(f"Successfully deleted {initial_rows_no - final_rows_no} rows from table {table_name}")


def add_bulk_csv_to_table(table_name, csv_path):
    df = pd.read_csv(csv_path)
    # replace nan/empty values with "None"
    df = df.where(pd.notnull(df), None)

    col_to_insert = list(df.columns)
    val_to_insert = df.values.tolist()
    add_bulk_rows_into_db(table_name, col_to_insert, val_to_insert)


# Delete specified identifiers
# env = "testnet"
# delete_strings = ["testnet_37"]
# for del_str in delete_strings:
#     delete_record(env, "identifier", del_str)
#     delete_record(env + "_epoch_duration", "identifier", del_str)
#     delete_record(env + "_logs", "identifier", del_str)
