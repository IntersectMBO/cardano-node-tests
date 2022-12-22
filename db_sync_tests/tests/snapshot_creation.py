import argparse
import json
import os
import sys
import time
from pathlib import Path
from collections import OrderedDict

sys.path.append(os.getcwd())

from utils.utils import seconds_to_time, get_no_of_cpu_cores, \
    get_current_date_time, get_os_type, get_total_ram_in_GB, \
    upload_artifact, print_file, create_db_sync_snapshot_stage_2, \
    write_data_as_json_to_file, set_buildkite_meta_data, \
    get_db_sync_version ,get_environment, get_db_pr, should_skip, \
    get_db_sync_branch, get_db_sync_version_from_gh_action, sh_colors, \
    get_file_size, create_db_sync_snapshot_stage_1, print_color_log, \
    ROOT_TEST_PATH, ENVIRONMENT

from utils.aws_db_utils import get_identifier_last_run_from_table, add_single_row_into_db



TEST_RESULTS = f"snapshot_creation_{ENVIRONMENT}_test_results.json"


def upload_snapshot_creation_results_to_aws(env):
    print("--- Write snapshot creation results to AWS Database")
    with open(TEST_RESULTS, "r") as json_file:
        db_snapshot_creation_test_results_dict = json.load(json_file)

    db_snapshot_creation_test_summary_table = env + '_db_sync_snapshot_creation'
    test_id = str(int(get_identifier_last_run_from_table(db_snapshot_creation_test_summary_table).split("_")[-1]) + 1)
    identifier = env + "_" + test_id
    db_snapshot_creation_test_results_dict["identifier"] = identifier

    print(f"  ==== Write test values into the {db_snapshot_creation_test_summary_table} DB table:")
    col_to_insert = list(db_snapshot_creation_test_results_dict.keys())
    val_to_insert = list(db_snapshot_creation_test_results_dict.values())

    if not add_single_row_into_db(db_snapshot_creation_test_summary_table, col_to_insert, val_to_insert):
        print(f"col_to_insert: {col_to_insert}")
        print(f"val_to_insert: {val_to_insert}")
        exit(1)


def main():
    if should_skip(args) == "true":
        print("--- Skipping Db sync snapshot creation")
        return 0

    print("--- Db sync snapshot creation")
    platform_system, platform_release, platform_version = get_os_type()
    start_test_time = get_current_date_time()
    print(f"Test start time: {start_test_time}")

    env = get_environment(args)
    print(f"Environment: {env}")

    db_sync_version, db_sync_git_rev = get_db_sync_version()
    print(f"DB-Sync version: {db_sync_version}")
    print(f"DB-Sync revision: {db_sync_git_rev}")

    db_sync_pr = get_db_pr(args)
    print(f"DB-Sync PR: {db_sync_pr}")

    db_branch = get_db_sync_branch(args)
    print(f"DB sync branch: {db_branch}")

    db_sync_version_from_gh_action = get_db_sync_version_from_gh_action(args)
    print(f"DB sync GH version: {db_sync_version_from_gh_action}")


    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    start_snapshot_creation = time.perf_counter()
    stage_2_cmd = create_db_sync_snapshot_stage_1(env)
    print(f"Stage 2 command: {stage_2_cmd}")
    stage_2_result = create_db_sync_snapshot_stage_2(stage_2_cmd, env)
    print(f"Stage 2 result: {stage_2_result}")
    end_snapshot_creation = time.perf_counter()

    snapshot_file = stage_2_result.split(" ")[1]
    set_buildkite_meta_data("snapshot_file", snapshot_file)
    print(f"Snapshot file name: {snapshot_file}")

    snapshot_creation_time_seconds = int(end_snapshot_creation - start_snapshot_creation)
    print(f"Snapshot creation time [seconds]: {snapshot_creation_time_seconds}")

    end_test_time = get_current_date_time()
    print(f"Test end time: {end_test_time}")

    # export test data as a json file
    test_data = OrderedDict()
    test_data["platform_system"] = platform_system
    test_data["platform_release"] = platform_release
    test_data["platform_version"] = platform_version
    test_data["no_of_cpu_cores"] = get_no_of_cpu_cores()
    test_data["total_ram_in_GB"] = get_total_ram_in_GB()
    test_data["env"] = env
    test_data["db_sync_branch"] = db_branch
    test_data["db_version"] = db_sync_version_from_gh_action
    test_data["db_sync_version"] = db_sync_version
    test_data["db_sync_git_rev"] = db_sync_git_rev
    test_data["start_test_time"] = start_test_time
    test_data["end_test_time"] = end_test_time
    test_data["snapshot_creation_time_in_sec"] = snapshot_creation_time_seconds
    test_data["snapshot_creation_time_in_h_m_s"] = seconds_to_time(int(snapshot_creation_time_seconds))
    test_data["snapshot_size_in_mb"] = get_file_size(snapshot_file)
    test_data["stage_2_cmd"] = stage_2_cmd
    test_data["stage_2_result"] = stage_2_result

    write_data_as_json_to_file(TEST_RESULTS, test_data)
    print_file(TEST_RESULTS)


    # upload artifacts
    upload_artifact(TEST_RESULTS)
    
    if env != "mainnet":
        upload_artifact(snapshot_file)

    # send results to aws database
    upload_snapshot_creation_results_to_aws(env)

    print('--- Summary: snapshot creation details')
    snapsot_creation_outcome = test_data["stage_2_result"]
    print_color_log(sh_colors.WARNING, f"Snapshot creation script result: {snapsot_creation_outcome}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute basic sync test\n\n")

    parser.add_argument(
        "-dpr", "--db_sync_pr", help="db-sync pr"
    )
    parser.add_argument(
        "-dbr", "--db_sync_branch", help="db-sync branch"
    )
    parser.add_argument(
        "-dv", "--db_sync_version_gh_action", help="db-sync version - 12.0.0-rc2 (tag number) or 12.0.2 (release number - for released versions) or 12.0.2_PR2124 (for not released and not tagged runs with a specific db_sync PR/version)"
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