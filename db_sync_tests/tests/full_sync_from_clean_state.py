import argparse
import json
import os
from collections import OrderedDict
from pathlib import Path
import sys
import matplotlib.pyplot as plt

sys.path.append(os.getcwd())

from utils.utils import seconds_to_time, get_no_of_cpu_cores, get_current_date_time, \
    get_os_type, get_total_ram_in_GB, upload_artifact, clone_repo, zip_file, execute_command, \
    print_file, stop_process, write_data_as_json_to_file, get_node_config_files, \
    get_node_version, get_db_sync_version, start_node_in_cwd, wait_for_db_to_sync, \
    set_node_socket_path_env_var_in_cwd, get_db_sync_tip, get_db_sync_progress, \
    get_total_db_size , print_color_log, copy_node_executables, are_rollbacks_present_in_db_sync_logs, \
    export_epoch_sync_times_from_db, are_errors_present_in_db_sync_logs, is_string_present_in_file, \
    setup_postgres, get_environment, get_node_pr, get_node_branch, get_node_version_from_gh_action, \
    get_db_sync_branch, get_db_sync_start_options, get_db_sync_version_from_gh_action, \
    start_db_sync, create_database, create_node_database_archive, print_color_log, check_database, \
    create_pgpass_file, get_last_perf_stats_point, copy_db_sync_executables, get_db_schema, \
    get_db_indexes, db_sync_perf_stats, sh_colors, ONE_MINUTE, ROOT_TEST_PATH, POSTGRES_DIR, POSTGRES_USER, \
    DB_SYNC_PERF_STATS, NODE_LOG, DB_SYNC_LOG, EPOCH_SYNC_TIMES, PERF_STATS_ARCHIVE, \
    NODE_ARCHIVE, DB_SYNC_ARCHIVE, SYNC_DATA_ARCHIVE, EXPECTED_DB_SCHEMA, EXPECTED_DB_INDEXES, \
    ENVIRONMENT \

from utils.aws_db_utils import get_identifier_last_run_from_table, \
    add_bulk_rows_into_db, add_single_row_into_db



TEST_RESULTS = f"db_sync_{ENVIRONMENT}_full_sync_test_results.json"
CHART = f"full_sync_{ENVIRONMENT}_stats_chart.png"

def create_sync_stats_chart():
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    fig = plt.figure(figsize = (14, 10))

    # define epochs sync times chart
    ax_epochs =  fig.add_axes([0.05, 0.05, 0.9, 0.35])
    ax_epochs.set(xlabel='epochs [number]', ylabel='time [min]')
    ax_epochs.set_title('Epochs Sync Times')

    with open(EPOCH_SYNC_TIMES, "r") as json_db_dump_file:
        epoch_sync_times = json.load(json_db_dump_file)

    epochs = [ e['no'] for e in epoch_sync_times ]
    epoch_times = [ e['seconds']/60 for e in epoch_sync_times ]
    ax_epochs.bar(epochs, epoch_times)

    # define performance chart
    ax_perf =  fig.add_axes([0.05, 0.5, 0.9, 0.45])
    ax_perf.set(xlabel='time [min]', ylabel='RSS [B]')
    ax_perf.set_title('RSS usage')

    with open(DB_SYNC_PERF_STATS, "r") as json_db_dump_file:
        perf_stats = json.load(json_db_dump_file)

    times = [ e['time']/60 for e in perf_stats ]
    rss_mem_usage = [ e['rss_mem_usage'] for e in perf_stats ]

    ax_perf.plot(times, rss_mem_usage)
    fig.savefig(CHART)


def upload_sync_results_to_aws(env):
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')

    print("--- Write full sync results to AWS Database")
    with open(TEST_RESULTS, "r") as json_file:
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

    with open(EPOCH_SYNC_TIMES, "r") as json_db_dump_file:
        epoch_sync_times = json.load(json_db_dump_file)

    epoch_duration_table = env + '_epoch_duration_db_sync'
    print(f"  ==== Write test values into the {epoch_duration_table} DB table:")
    col_to_insert = ['identifier', 'epoch_no', 'sync_duration_secs']
    val_to_insert = [ (identifier, e['no'], e['seconds']) for e in epoch_sync_times ]

    if not add_bulk_rows_into_db(epoch_duration_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)

    with open(DB_SYNC_PERF_STATS, "r") as json_perf_stats_file:
        db_sync_performance_stats = json.load(json_perf_stats_file)

    db_sync_performance_stats_table = env + '_performance_stats_db_sync'
    print(f"  ==== Write test values into the {db_sync_performance_stats_table} DB table:")
    col_to_insert = ['identifier', 'time', 'slot_no', 'cpu_percent_usage', 'rss_mem_usage']
    val_to_insert = [ (identifier, e['time'], e['slot_no'], e['cpu_percent_usage'], e['rss_mem_usage']) for e in db_sync_performance_stats ]

    if not add_bulk_rows_into_db(db_sync_performance_stats_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)


def print_report(db_schema, db_indexes):
    log_errors = are_errors_present_in_db_sync_logs(DB_SYNC_LOG)
    print_color_log(sh_colors.WARNING, f"Are errors present: {log_errors}")

    rollbacks = are_rollbacks_present_in_db_sync_logs(DB_SYNC_LOG)
    print_color_log(sh_colors.WARNING, f"Are rollbacks present: {rollbacks}")

    failed_rollbacks = is_string_present_in_file(DB_SYNC_LOG, "Rollback failed")
    print_color_log(sh_colors.WARNING, f"Are failed rollbacks present: {failed_rollbacks}")

    corrupted_ledger_files = is_string_present_in_file(DB_SYNC_LOG, "Failed to parse ledger state")
    print_color_log(sh_colors.WARNING, f"Are corrupted ledger files present: {corrupted_ledger_files}")

    if db_schema:
        print_color_log(sh_colors.WARNING, f"Db schema issues: {db_schema}")
    else:
        print_color_log(sh_colors.WARNING, f"NO Db schema issues")
    if db_indexes:
        print_color_log(sh_colors.WARNING, f"Db indexes issues: {db_indexes}")
    else:
        print_color_log(sh_colors.WARNING, f"NO Db indexes issues")



def main():

    # system and software versions details
    print("--- Sync from clean state - setup")
    platform_system, platform_release, platform_version = get_os_type()
    print(f"Platform: {platform_system, platform_release, platform_version}")

    start_test_time = get_current_date_time()
    print(f"Test start time: {start_test_time}")

    env = get_environment(args)
    print(f"Environment: {env}")

    node_pr = get_node_pr(args)
    print(f"Node PR number: {node_pr}")

    node_branch = get_node_branch(args)
    print(f"Node branch: {node_branch}")

    node_version_from_gh_action = get_node_version_from_gh_action(args)
    print(f"Node version: {node_version_from_gh_action}")

    db_branch = get_db_sync_branch(args)
    print(f"DB sync branch: {db_branch}")

    db_start_options = get_db_sync_start_options(args)

    db_sync_version_from_gh_action = get_db_sync_version_from_gh_action(args) + " " + db_start_options
    print(f"DB sync version: {db_sync_version_from_gh_action}")

    # cardano-node setup
    NODE_DIR=clone_repo('cardano-node', node_branch)
    os.chdir(NODE_DIR)
    execute_command("nix build -v .#cardano-node -o cardano-node-bin")
    execute_command("nix build -v .#cardano-cli -o cardano-cli-bin")
    print("--- Node setup")
    copy_node_executables(build_method="nix")
    get_node_config_files(env)
    set_node_socket_path_env_var_in_cwd()
    cli_version, cli_git_rev = get_node_version()
    start_node_in_cwd(env)
    print("--- Node startup", flush=True)
    print_file(NODE_LOG, 80)


    # cardano-db sync setup
    os.chdir(ROOT_TEST_PATH)
    DB_SYNC_DIR = clone_repo('cardano-db-sync', db_branch)
    os.chdir(DB_SYNC_DIR)
    print("--- Db sync setup")
    setup_postgres() # To login use: psql -h /path/to/postgres -p 5432 -e postgres
    create_pgpass_file(env)
    create_database()
    execute_command("nix build -v .#cardano-db-sync -o db-sync-node")
    execute_command("nix build -v .#cardano-db-tool -o db-sync-tool")
    copy_db_sync_executables(build_method="nix")
    print("--- Db sync startup", flush=True)
    start_db_sync(env, start_args=db_start_options)
    db_sync_version, db_sync_git_rev = get_db_sync_version()
    print_file(DB_SYNC_LOG, 30)
    db_full_sync_time_in_secs = wait_for_db_to_sync(env)
    print("--- Db sync schema and indexes check for erors")
    db_schema = check_database(get_db_schema, 'DB schema is incorrect', EXPECTED_DB_SCHEMA)
    db_indexes = check_database(get_db_indexes, 'DB indexes are incorrect', EXPECTED_DB_INDEXES)
    epoch_no, block_no, slot_no = get_db_sync_tip(env)
    end_test_time = get_current_date_time()

    print("--- Summary & Artifacts uploading")
    print(f"FINAL db-sync progress: {get_db_sync_progress(env)}, epoch: {epoch_no}, block: {block_no}")
    print(f"TOTAL sync time [sec]: {db_full_sync_time_in_secs}")

    # shut down services
    stop_process('cardano-db-sync')
    stop_process('cardano-node')

    # export test data as a json file
    test_data = OrderedDict()
    test_data["platform_system"] = platform_system
    test_data["platform_release"] = platform_release
    test_data["platform_version"] = platform_version
    test_data["no_of_cpu_cores"] = get_no_of_cpu_cores()
    test_data["total_ram_in_GB"] = get_total_ram_in_GB()
    test_data["env"] = env
    test_data["node_pr"] = node_pr
    test_data["node_branch"] = node_branch
    test_data["node_version"] = node_version_from_gh_action
    test_data["db_sync_branch"] = db_branch
    test_data["db_version"] = db_sync_version_from_gh_action
    test_data["node_cli_version"] = cli_version
    test_data["node_git_revision"] = cli_git_rev
    test_data["db_sync_version"] = db_sync_version
    test_data["db_sync_git_rev"] = db_sync_git_rev
    test_data["start_test_time"] = start_test_time
    test_data["end_test_time"] = end_test_time
    test_data["total_sync_time_in_sec"] = db_full_sync_time_in_secs
    test_data["total_sync_time_in_h_m_s"] = seconds_to_time(int(db_full_sync_time_in_secs))
    test_data["last_synced_epoch_no"] = epoch_no
    test_data["last_synced_block_no"] = block_no
    test_data["last_synced_slot_no"] = slot_no
    last_perf_stats_data_point = get_last_perf_stats_point()
    test_data["cpu_percent_usage"] = last_perf_stats_data_point["cpu_percent_usage"]
    test_data["total_rss_memory_usage_in_B"] = last_perf_stats_data_point["rss_mem_usage"]
    test_data["total_database_size"] = get_total_db_size(env)
    test_data["rollbacks"] = are_rollbacks_present_in_db_sync_logs(DB_SYNC_LOG)
    test_data["errors"] = are_errors_present_in_db_sync_logs(DB_SYNC_LOG)

    write_data_as_json_to_file(TEST_RESULTS, test_data)
    write_data_as_json_to_file(DB_SYNC_PERF_STATS, db_sync_perf_stats)
    export_epoch_sync_times_from_db(env, EPOCH_SYNC_TIMES)

    print_file(TEST_RESULTS)

    # compress artifacts
    zip_file(NODE_ARCHIVE, NODE_LOG)
    zip_file(DB_SYNC_ARCHIVE, DB_SYNC_LOG)
    zip_file(SYNC_DATA_ARCHIVE, EPOCH_SYNC_TIMES)
    zip_file(PERF_STATS_ARCHIVE, DB_SYNC_PERF_STATS)

    # upload artifacts
    upload_artifact(NODE_ARCHIVE)
    upload_artifact(DB_SYNC_ARCHIVE)
    upload_artifact(SYNC_DATA_ARCHIVE)
    upload_artifact(PERF_STATS_ARCHIVE)
    upload_artifact(TEST_RESULTS)

    # send results to aws database
    upload_sync_results_to_aws(env)

    # create and upload plot
    create_sync_stats_chart()
    upload_artifact(CHART)

    # create and upload compressed node db archive
    if env != "mainnet":
        node_db = create_node_database_archive(env)
        upload_artifact (node_db)

    # search db-sync log for issues
    print("--- Summary: Rollbacks, errors and other isssues")
    print_report(db_schema, db_indexes)


if __name__ == "__main__":

    def hyphenated(db_sync_start_args):
        start_args = db_sync_start_args.split(" ")
        final_args_string=''

        for arg in start_args:
            final_args_string+=str("--" + arg + " ")

        return final_args_string

    parser = argparse.ArgumentParser(description="Execute basic sync test\n\n")

    parser.add_argument(
        "-npr", "--node_pr", help="node pr number"
    )
    parser.add_argument(
        "-nbr", "--node_branch", help="node branch or tag"
    )
    parser.add_argument(
        "-nv", "--node_version_gh_action", help="node version - 1.33.0-rc2 (tag number) or 1.33.0 (release number - for released versions) or 1.33.0_PR2124 (for not released and not tagged runs with a specific node PR/version)"
    )
    parser.add_argument(
        "-dbr", "--db_sync_branch", help="db-sync branch or tag"
    )
    parser.add_argument(
        "-dv", "--db_sync_version_gh_action", help="db-sync version - 12.0.0-rc2 (tag number) or 12.0.2 (release number - for released versions) or 12.0.2_PR2124 (for not released and not tagged runs with a specific db_sync PR/version)"
    )
    parser.add_argument(
        "-dsa", "--db_sync_start_options", type=hyphenated, help="db-sync start arguments: --disable-ledger, --disable-cache, --disable-epoch"
    )
    parser.add_argument(
        "-e",
        "--environment",
        help="the environment on which to run the tests - shelley_qa, testnet, staging or mainnet.",
    )

    args = parser.parse_args()

    main()
