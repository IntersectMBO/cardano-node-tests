import pandas as pd

from explorer_utils import get_tx_count_per_epoch_from_explorer, get_current_epoch_no_from_explorer
from aws_db_utils import get_last_epoch_no_from_table, add_bulk_values_into_db
from sync_tests.blockfrost_utils import get_tx_count_per_epoch_from_blockfrost, \
    get_current_epoch_no_from_blockfrost


def update_mainnet_tx_count_per_epoch():
    table_name = "mainnet_tx_count"
    env = "mainnet"
    if env == "mainnet":
        current_epoch_no = get_current_epoch_no_from_blockfrost()
    else:
        current_epoch_no = get_current_epoch_no_from_explorer(env)
    last_added_epoch_no = int(get_last_epoch_no_from_table(table_name))

    print(f"last_added_epoch_no: {last_added_epoch_no}")
    print(f"current_epoch_no   : {current_epoch_no}")

    df_column_names = ["epoch_no", "tx_count"]
    df = pd.DataFrame(columns=df_column_names)

    if current_epoch_no > last_added_epoch_no + 1:
        # adding values into the db only for missing full epochs (ignoring the current/incomplete epoch)
        for epoch_no in range(last_added_epoch_no + 1, current_epoch_no):
            print(f"Getting values for epoch {epoch_no}")
            if env == "mainnet":
                tx_count = get_tx_count_per_epoch_from_blockfrost(epoch_no)
            else:
                tx_count = get_tx_count_per_epoch_from_explorer(env, epoch_no)
            print(f"  - tx_count: {tx_count}")
            new_row = {"epoch_no": epoch_no,
                       "tx_count": tx_count}
            df = df.append(new_row, ignore_index=True)

        col_to_insert = list(df.columns)
        val_to_insert = df.values.tolist()
        if not add_bulk_values_into_db(table_name, col_to_insert, val_to_insert):
            print(f"col_to_insert: {col_to_insert}")
            print(f"val_to_insert: {val_to_insert}")
    else:
        print("There are no new finalized epochs to be added")


if __name__ == "__main__":
    update_mainnet_tx_count_per_epoch()
