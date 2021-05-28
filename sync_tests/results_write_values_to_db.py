import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
import argparse

import requests

from sqlite_utils import add_test_values_into_db, get_column_values, export_db_table_to_csv
from utils import seconds_to_time, date_diff_in_seconds

DATABASE_NAME = r"automated_tests_results.db"
ORG_SLUG = "input-output-hk"
cli_envs = ["node_cli", "dbsync_cli"]
nightly_envs = ["node_nightly", "dbsync_nightly"]


def get_buildkite_pipeline_builds(buildkite_token, pipeline_slug):
    url = f"https://api.buildkite.com/v2/organizations/{ORG_SLUG}/pipelines/{pipeline_slug}/builds"
    web_url = f"https://buildkite.com/{ORG_SLUG}/{pipeline_slug}"
    headers = {'Authorization': "Bearer " + buildkite_token}
    print(f"url: {url}")
    print(f"web_url: {web_url}")
    response = requests.get(url, headers=headers)

    status_code = response.status_code
    if status_code == 200:
        return response.json()
    else:
        print(f"status_code: {status_code}")
        print(f"response   : {response.json()}")
        print(f"!!! ERROR: status_code =! 200 when getting the builds for {pipeline_slug} pipeline")
        exit(1)


def get_buildkite_pipeline_slug(env):
    pileline_slug = None
    if env == "node_cli":
        pileline_slug = "cardano-node-tests"
    elif env == "node_nightly":
        pileline_slug = "cardano-node-tests-nightly"
    elif env == "dbsync_cli":
        pileline_slug = "cardano-node-tests-dbsync"
    elif env == "dbsync_nightly":
        pileline_slug = "cardano-node-tests-nightly-dbsync"
    else:
        print(f"!!! ERROR: env {env} not expected - use one of: {cli_envs + nightly_envs}")
        exit(1)
    return pileline_slug


def main():
    secret = vars(args)["secret"]

    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print("  ==== Move to 'sync_tests' directory")
    os.chdir(current_directory / "sync_tests")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    database_path = Path(current_directory) / DATABASE_NAME
    print(f"database_path: {database_path}")

    for env in cli_envs:
        print(f" === env: {env}")
        pileline_slug = get_buildkite_pipeline_slug(env)
        pipeline_builds = get_buildkite_pipeline_builds(secret, pileline_slug)

        print(f" - there are {len(pipeline_builds)} builds")
        print("Adding build results into the DB")
        build_results_dict = OrderedDict()
        for build in pipeline_builds:
            # don't add the same build no twice
            if build["state"] == "running":
                print(f"  ==== build no {build['number']} is still running; not adding it into the DB yet")
                continue
            if build["number"] not in get_column_values(database_path, env, "build_no"):
                build_results_dict["build_no"] = build["number"]
                build_results_dict["build_id"] = build["id"]
                build_results_dict["build_web_url"] = build["web_url"]
                build_results_dict["build_status"] = build["state"]
                build_results_dict["build_started_at"] = build["started_at"]
                build_results_dict["build_finished_at"] = build["finished_at"]
                build_results_dict["build_duration"] = seconds_to_time(date_diff_in_seconds(
                    datetime.strptime(build_results_dict["build_finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ"),
                    datetime.strptime(build_results_dict["build_started_at"], "%Y-%m-%dT%H:%M:%S.%fZ")))
                build_results_dict["test_branch"] = build["branch"]
                build_results_dict["node_branch"] = build["env"]["NODE_BRANCH"]
                build_results_dict["node_rev"] = build["env"]["NODE_REV"]
                build_results_dict["cluster_era"] = build["env"]["CLUSTER_ERA"]
                build_results_dict["tx_era"] = build["env"]["TX_ERA"]
                if "dbsync" in env:
                    build_results_dict["dbsync_branch"] = build["env"]["DBSYNC_BRANCH"]
                    build_results_dict["dbsync_rev"] = build["env"]["DBSYNC_REV"]

                print(f"  ==== Writing test results into the {env} DB table for build no {build['number']}")
                col_list = list(build_results_dict.keys())
                col_values = list(build_results_dict.values())

                if not add_test_values_into_db(database_path, env, col_list, col_values):
                    print(f"col_list  : {col_list}")
                    print(f"col_values: {col_values}")
                    exit(1)
            else:
                print(f" -- results for build {build['number']} are already into the DB")

        print(f"  ==== Exporting the {env} table as CSV")
        export_db_table_to_csv(database_path, env)

    for env in nightly_envs:
        print(f" === env: {env}")
        pileline_slug = get_buildkite_pipeline_slug(env)
        pipeline_builds = get_buildkite_pipeline_builds(secret, pileline_slug)

        print(f" - there are {len(pipeline_builds)} builds")
        print("Adding build results into the DB")
        build_results_dict = OrderedDict()
        for build in pipeline_builds:
            # don't add the same build no twice
            if (env == "dbsync_nightly") & (build["number"] < 47):
                continue
            if build["state"] == "running":
                print(f"  ==== build no {build['number']} is still running; not adding it into the DB yet")
                continue
            if build["number"] not in get_column_values(database_path, env, "build_no"):
                build_results_dict["build_no"] = build["number"]
                build_results_dict["build_id"] = build["id"]
                build_results_dict["build_web_url"] = build["web_url"]
                build_results_dict["build_status"] = build["state"]
                build_results_dict["build_started_at"] = build["started_at"]
                build_results_dict["build_finished_at"] = build["finished_at"]
                build_results_dict["build_duration"] = seconds_to_time(date_diff_in_seconds(
                    datetime.strptime(build_results_dict["build_finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ"),
                    datetime.strptime(build_results_dict["build_started_at"], "%Y-%m-%dT%H:%M:%S.%fZ")))
                build_results_dict["test_branch"] = build["branch"]
                build_results_dict["node_branch"] = "master"
                if "dbsync" in env:
                    build_results_dict["dbsync_branch"] = "master"

                print(f"  ==== Writing test results into the {env} DB table for build no {build['number']}")
                col_list = list(build_results_dict.keys())
                col_values = list(build_results_dict.values())

                if not add_test_values_into_db(database_path, env, col_list, col_values):
                    print(f"col_list  : {col_list}")
                    print(f"col_values: {col_values}")
                    exit(1)
            else:
                print(f" -- results for build {build['number']} are already into the DB")

        print(f"  ==== Exporting the {env} table as CSV")
        export_db_table_to_csv(database_path, env)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get node cli nightly results\n\n")

    parser.add_argument(
        "-t", "--secret", help="buildkite secret"
    )

    args = parser.parse_args()

    main()