import os
import sys
import argparse

from collections import OrderedDict
from pathlib import Path

sys.path.append(os.getcwd())

from utils.utils import seconds_to_time, date_diff_in_seconds, get_no_of_cpu_cores, \
    get_current_date_time, get_os_type, get_directory_size, get_total_ram_in_GB, \
    upload_artifact, clone_repo, print_file, stop_process, export_env_var, create_dir, \
    zip_file, get_process_info, write_data_as_json_to_file, get_testnet_value, \
    get_node_archive_url, get_and_extract_archive_files, print_n_last_lines_from_file, \
    get_node_config_files, get_node_version, get_db_sync_version, start_node_in_cwd, \
    get_node_tip, set_node_socket_path_env_var_in_cwd, get_db_sync_tip, \
    export_epoch_sync_times_from_db, emergency_upload_artifacts, get_total_db_size, \
    wait_for_db_to_sync, get_db_sync_progress, remove_dir, get_build_meta_data, \
    setup_postgres , get_environment, get_node_pr, get_node_branch, get_node_version_from_gh_action, \
    get_db_sync_branch, get_db_sync_start_options, get_db_sync_version_from_gh_action, \
    start_db_sync, wait_for_node_to_sync, are_errors_present_in_db_sync_logs, get_db_sync_snaphot_size, \
    are_rollbacks_present_in_db_sync_logs, create_database, wait, create_pgpass_file, \
    restore_db_sync_from_snapshot, should_skip, \
    ONE_MINUTE, ROOT_TEST_PATH, POSTGRES_DIR, POSTGRES_USER,\
    NODE_LOG_FILE_PATH, DB_SYNC_LOG_FILE_PATH \



TEST_RESULTS_FILE_NAME = 'db_sync_local_snapshot_restoration_test_results.json'
DB_SYNC_RESTORATION_ARCHIVE = 'cardano_db_sync_restoration.zip'


def main():
    if should_skip(args) == "true":
        print("--- Skipping Db sync snapshot restoration")
        return 0

    print("--- Db sync snapshot restoration")

    # system and software versions details
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

    # database setup
    setup_postgres(pg_port='5433')
    create_pgpass_file(env)
    create_database()

    # snapshot restoration 
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    snapshot_file = get_build_meta_data("snapshot_file")
    print(f"Snapshot file from key-store: {snapshot_file}")
    restoration_time = restore_db_sync_from_snapshot(env, snapshot_file)
    print(f"Restoration time [sec]: {restoration_time}")
    snapshot_epoch_no, snapshot_block_no, snapshot_slot_no = get_db_sync_tip(env)
    print(f"db-sync tip after snapshot restoration: epoch: {snapshot_epoch_no}, block: {snapshot_block_no}, slot: {snapshot_slot_no}")
    
    #start node
    print("--- Node startup after snapshot restoration")
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-node')
    set_node_socket_path_env_var_in_cwd()
    start_node_in_cwd(env)
    print_file(NODE_LOG_FILE_PATH, 80)
    wait_for_node_to_sync(env)

    #start db-sync
    print("--- Db-sync startup after snapshot restoration")
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    export_env_var("PGPORT", '5433')
    start_db_sync(env, start_args="", first_start="False")
    wait(ONE_MINUTE)
    db_sync_version, db_sync_git_rev = get_db_sync_version()
    db_full_sync_time_in_secs = wait_for_db_to_sync(env)
    end_test_time = get_current_date_time()
    wait(30 * ONE_MINUTE)
    epoch_no, block_no, slot_no = get_db_sync_tip(env)
    print(f"Test end time: {end_test_time}")
    print_file(DB_SYNC_LOG_FILE_PATH, 30)

    #stop cardano-node and cardano-db-sync
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
    test_data["db_sync_version"] = db_sync_version
    test_data["db_sync_git_rev"] = db_sync_git_rev
    test_data["start_test_time"] = start_test_time
    test_data["end_test_time"] = end_test_time
    test_data["db_total_sync_time_in_sec"] = db_full_sync_time_in_secs
    test_data["db_total_sync_time_in_h_m_s"] = seconds_to_time(int(db_full_sync_time_in_secs))
    test_data["snapshot_name"] = snapshot_file
    test_data["snapshot_size_in_mb"] = get_db_sync_snaphot_size(snapshot_file)
    test_data["restoration_time"] = restoration_time
    test_data["snapshot_epoch_no"] = snapshot_epoch_no
    test_data["snapshot_block_no"] = snapshot_block_no
    test_data["snapshot_slot_no"] = snapshot_slot_no
    test_data["last_synced_epoch_no"] = epoch_no
    test_data["last_synced_block_no"] = block_no
    test_data["last_synced_slot_no"] = slot_no
    test_data["total_database_size"] = get_total_db_size(env)
    test_data["rollbacks"] = are_rollbacks_present_in_db_sync_logs(DB_SYNC_LOG_FILE_PATH)
    test_data["errors"] = are_errors_present_in_db_sync_logs(DB_SYNC_LOG_FILE_PATH)

    write_data_as_json_to_file(TEST_RESULTS_FILE_NAME, test_data)
    print_file(TEST_RESULTS_FILE_NAME)

    # compress & upload artifacts
    zip_file(DB_SYNC_RESTORATION_ARCHIVE, DB_SYNC_LOG_FILE_PATH)
    upload_artifact(DB_SYNC_RESTORATION_ARCHIVE)
    upload_artifact(TEST_RESULTS_FILE_NAME)


if __name__ == "__main__":
    
    def hyphenated(string):
        return '--' + string

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
        "-dsa", "--db_sync_start_options", type=hyphenated, help="db-sync start arguments: --disable-ledger, --disable-cache, --disable-epoch"
    )
    parser.add_argument(
        "-e",
        "--environment",
        help="the environment on which to run the tests - shelley_qa, testnet, staging or mainnet.",
    )
    parser.add_argument(
        "-rosc", "--run_only_sync_test", help="should run only sync test ?"
    )

    args = parser.parse_args()

    main()