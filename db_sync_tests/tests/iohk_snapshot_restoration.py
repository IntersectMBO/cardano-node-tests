import os
import sys
import json
import argparse

from collections import OrderedDict
from pathlib import Path

sys.path.append(os.getcwd())

from utils.utils import seconds_to_time, get_no_of_cpu_cores, get_current_date_time, \
    get_os_type, get_total_ram_in_GB, upload_artifact, clone_repo, wait, zip_file, \
    print_file, stop_process, copy_node_executables, write_data_as_json_to_file, \
    execute_command, get_node_config_files, are_errors_present_in_db_sync_logs, \
    get_node_version, get_db_sync_version, start_node_in_cwd, wait_for_db_to_sync, \
    set_node_socket_path_env_var_in_cwd, get_db_sync_tip, \
    get_total_db_size , are_rollbacks_present_in_db_sync_logs, \
    export_epoch_sync_times_from_db, copy_db_sync_executables, print_color_log, \
    setup_postgres, get_environment, get_node_pr, get_node_branch, \
    get_db_sync_branch, get_db_sync_version_from_gh_action, get_node_version_from_gh_action, \
    start_db_sync, create_database, download_and_extract_node_snapshot, \
    wait_for_node_to_sync, list_databases, create_pgpass_file, restore_db_sync_from_snapshot, \
    download_db_sync_snapshot, get_snapshot_sha_256_sum, get_file_sha_256_sum, \
    get_latest_snapshot_url, is_string_present_in_file, get_last_perf_stats_point, \
    db_sync_perf_stats, sh_colors, ONE_MINUTE, ROOT_TEST_PATH, POSTGRES_DIR, POSTGRES_USER, \
    DB_SYNC_PERF_STATS, NODE_LOG, DB_SYNC_LOG, EPOCH_SYNC_TIMES, PERF_STATS_ARCHIVE, \
    NODE_ARCHIVE, DB_SYNC_ARCHIVE, SYNC_DATA_ARCHIVE, ENVIRONMENT \

from utils.aws_db_utils import get_identifier_last_run_from_table, \
    add_bulk_rows_into_db, add_single_row_into_db



TEST_RESULTS = 'db_sync_iohk_snapshot_restoration_test_results.json'


def upload_snapshot_restoration_results_to_aws(env):
    print("--- Write IOHK snapshot restoration results to AWS Database")
    with open(TEST_RESULTS, "r") as json_file:
        sync_test_results_dict = json.load(json_file)

    test_summary_table = env + '_db_sync_snapshot_restoration'
    test_id = str(int(get_identifier_last_run_from_table(test_summary_table).split("_")[-1]) + 1)
    identifier = env + "_restoration_" + test_id
    sync_test_results_dict["identifier"] = identifier

    print(f"  ==== Write test values into the {test_summary_table} DB table:")
    col_to_insert = list(sync_test_results_dict.keys())
    val_to_insert = list(sync_test_results_dict.values())

    if not add_single_row_into_db(test_summary_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)


def main():

    print("--- Db-sync restoration from IOHK official snapshot")
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

    db_sync_version_from_gh_action = get_db_sync_version_from_gh_action(args)
    print(f"DB sync version: {db_sync_version_from_gh_action}")

    snapshot_url = get_latest_snapshot_url(env, args)
    print(f"Snapshot url: {snapshot_url}")

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
    download_and_extract_node_snapshot(env)
    start_node_in_cwd(env)
    print("--- Node startup", flush=True)
    print_file(NODE_LOG, 80)
    node_sync_time_in_secs = wait_for_node_to_sync(env)

    # cardano-db sync setup
    print("--- Db sync setup")
    os.chdir(ROOT_TEST_PATH)
    DB_SYNC_DIR = clone_repo('cardano-db-sync', db_branch)
    os.chdir(DB_SYNC_DIR)
    setup_postgres()
    create_pgpass_file(env)
    create_database()
    list_databases()
    execute_command("nix build .#cardano-db-sync -o db-sync-node")
    execute_command("nix build .#cardano-db-tool -o db-sync-tool")
    print("--- Download and check db-sync snapshot", flush=True)
    copy_db_sync_executables(build_method="nix")
    snapshot_name = download_db_sync_snapshot(snapshot_url)
    expected_snapshot_sha_256_sum = get_snapshot_sha_256_sum(snapshot_url)
    actual_snapshot_sha_256_sum = get_file_sha_256_sum(snapshot_name)
    assert expected_snapshot_sha_256_sum == actual_snapshot_sha_256_sum, "Incorrect sha 256 sum"

    # restore snapshot
    print("--- Snapshot restoration")
    restoration_time = restore_db_sync_from_snapshot(env, snapshot_name, remove_ledger_dir="no")
    print(f"Restoration time [sec]: {restoration_time}")
    snapshot_epoch_no, snapshot_block_no, snapshot_slot_no = get_db_sync_tip(env)
    print(f"db-sync tip after restoration: epoch: {snapshot_epoch_no}, block: {snapshot_block_no}, slot: {snapshot_slot_no}")

    # start db-sync
    print("--- Db sync start")
    start_db_sync(env, start_args="", first_start="True")
    print_file(DB_SYNC_LOG, 30)
    db_sync_version, db_sync_git_rev = get_db_sync_version()
    db_full_sync_time_in_secs = wait_for_db_to_sync(env)
    end_test_time = get_current_date_time()
    wait_time = 30
    print(f"Waiting for additional {wait_time} minutes to continue syncying...")
    wait(wait_time * ONE_MINUTE)
    print_file(DB_SYNC_LOG, 60)
    epoch_no, block_no, slot_no = get_db_sync_tip(env)

    # shut down services
    print("--- Stop cardano services")
    stop_process('cardano-db-sync')
    stop_process('cardano-node')

    # export test data as a json file
    print("--- Gathering end results")
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
    test_data["node_total_sync_time_in_sec"] = node_sync_time_in_secs
    test_data["node_total_sync_time_in_h_m_s"] = seconds_to_time(int(node_sync_time_in_secs))
    test_data["db_total_sync_time_in_sec"] = db_full_sync_time_in_secs
    test_data["db_total_sync_time_in_h_m_s"] = seconds_to_time(int(db_full_sync_time_in_secs))
    test_data["snapshot_url"] = snapshot_url
    test_data["snapshot_name"] = snapshot_name
    test_data["snapshot_epoch_no"] = snapshot_epoch_no
    test_data["snapshot_block_no"] = snapshot_block_no
    test_data["snapshot_slot_no"] = snapshot_slot_no
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
    export_epoch_sync_times_from_db(env, EPOCH_SYNC_TIMES, snapshot_epoch_no)

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

    # send data to aws database
    upload_snapshot_restoration_results_to_aws(env)

    # search db-sync log for issues
    print("--- Summary: Rollbacks, errors and other isssues")

    log_errors = are_errors_present_in_db_sync_logs(DB_SYNC_LOG)
    print_color_log(sh_colors.WARNING, f"Are errors present: {log_errors}")

    rollbacks = are_rollbacks_present_in_db_sync_logs(DB_SYNC_LOG)
    print_color_log(sh_colors.WARNING, f"Are rollbacks present: {rollbacks}")

    failed_rollbacks = is_string_present_in_file(DB_SYNC_LOG, "Rollback failed")
    print_color_log(sh_colors.WARNING, f"Are failed rollbacks present: {failed_rollbacks}")

    corrupted_ledger_files = is_string_present_in_file(DB_SYNC_LOG, "Failed to parse ledger state")
    print_color_log(sh_colors.WARNING, f"Are corrupted ledger files present: {corrupted_ledger_files}")


if __name__ == "__main__":
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
        "-dbr", "--db_sync_branch", help="db-sync branch"
    )
    parser.add_argument(
        "-dv", "--db_sync_version_gh_action", help="db-sync version - 12.0.0-rc2 (tag number) or 12.0.2 (release number - for released versions) or 12.0.2_PR2124 (for not released and not tagged runs with a specific db_sync PR/version)"
    )
    parser.add_argument(
        "-surl", "--snapshot_url", help="snapshot download url"
    )
    parser.add_argument(
        "-e",
        "--environment",
        help="the environment on which to run the tests - shelley_qa, testnet, staging or mainnet.",
    )

    args = parser.parse_args()

    main()
