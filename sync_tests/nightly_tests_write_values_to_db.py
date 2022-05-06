import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import requests

from aws_db_utils import add_single_value_into_db, get_column_values
from utils import seconds_to_time, date_diff_in_seconds

ORG_SLUG = "input-output-hk"
cli_envs = ["node_cli", "dbsync_cli"]
nightly_envs = ["node_nightly", "node_nightly_shelley_tx", "node_nightly_mary_tx",
                "node_nightly_mary_tx_cddl", "node_nightly_p2p_cddl", "dbsync_nightly"]
DEFAULT_ERA = "alonzo"


def get_buildkite_pipeline_builds(buildkite_token, pipeline_slug):
    url = f"https://api.buildkite.com/v2/organizations/{ORG_SLUG}/pipelines/{pipeline_slug}/builds"
    web_url = f"https://buildkite.com/{ORG_SLUG}/{pipeline_slug}"
    print(f"url: {url}")
    print(f"web_url: {web_url}")

    headers = {'Authorization': "Bearer " + buildkite_token}
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
    elif env == "node_nightly_shelley_tx":
        pileline_slug = "cardano-node-tests-nightly-shelley-tx"
    elif env == "node_nightly_mary_tx":
        pileline_slug = "cardano-node-tests-nightly-mary-tx"
    elif env == "node_nightly_mary_tx_cddl":
        pileline_slug = "cardano-node-tests-nightly-p2p-cddl"
    elif env == "node_nightly_p2p_cddl":
        pileline_slug = "cardano-node-tests-nightly-cddl"
    elif env == "node_nightly_p2p":
        pileline_slug = "cardano-node-tests-nightly-p2p"
    else:
        print(f"!!! ERROR: env {env} not expected - use one of: {cli_envs + nightly_envs}")
        exit(1)
    return pileline_slug


def main():
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print("  ==== Move to 'sync_tests' directory")
    os.chdir(current_directory / "sync_tests")
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    for env in cli_envs:
        print(f" === env: {env}")
        pileline_slug = get_buildkite_pipeline_slug(env)
        pipeline_builds = get_buildkite_pipeline_builds(os.environ["BUILDKITE_API_ACCESS_TOKEN"], pileline_slug)
        print(f" - there are {len(pipeline_builds)} builds")
        print("Adding build results into the DB")
        build_results_dict = OrderedDict()
        for build in pipeline_builds:
            # don't add the same build_no twice
            if build["state"] == "running":
                print(f"  ==== build no {build['number']} is still running; not adding it into the DB yet")
                continue
            if build["number"] not in get_column_values(env, "build_no"):
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
                build_results_dict["node_branch"] = build["env"]["NODE_BRANCH"] if "NODE_BRANCH" in build["env"] else None
                build_results_dict["node_rev"] = build["env"]["NODE_REV"] if "NODE_REV" in build["env"] else None
                build_results_dict["cluster_era"] = build["env"]["CLUSTER_ERA"] if "CLUSTER_ERA" in build["env"] else None
                build_results_dict["tx_era"] = build["env"]["TX_ERA"] if "TX_ERA" in build["env"] else None
                if "dbsync" in env:
                    build_results_dict["dbsync_branch"] = build["env"]["DBSYNC_BRANCH"] if "DBSYNC_BRANCH" in build["env"] else None
                    build_results_dict["dbsync_rev"] = build["env"]["DBSYNC_REV"] if "DBSYNC_REV" in build["env"] else None

                print(f"  ==== Writing test results into the {env} DB table for build no {build['number']}")
                build_results_dict = {k: "None" if v == "" else v for k, v in build_results_dict.items()}
                col_to_insert = list(build_results_dict.keys())
                val_to_insert = list(build_results_dict.values())
                if not add_single_value_into_db(env, col_to_insert, val_to_insert):
                    print(f"col_to_insert: {col_to_insert}")
                    print(f"val_to_insert: {val_to_insert}")
                    exit(1)
            else:
                print(f" -- results for build {build['number']} are already into the DB")

    for env in nightly_envs:
        print(f" === env: {env}")
        pileline_slug = get_buildkite_pipeline_slug(env)
        pipeline_builds = get_buildkite_pipeline_builds(os.environ["BUILDKITE_API_ACCESS_TOKEN"], pileline_slug)
        table_name = env
        if "node_nightly" in env:
            table_name = "node_nightly"

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

            if build["web_url"] not in get_column_values(table_name, "build_web_url"):
                build_results_dict["details"] = env
                build_results_dict["build_no"] = build["number"]
                build_results_dict["build_id"] = build["id"]
                build_results_dict["build_web_url"] = build["web_url"]
                build_results_dict["build_status"] = build["state"]
                build_results_dict["build_started_at"] = build["started_at"]
                build_results_dict["build_finished_at"] = build["finished_at"]
                if "build_finished_at" in build_results_dict:
                    build_results_dict["build_duration"] = seconds_to_time(date_diff_in_seconds(
                        datetime.strptime(build_results_dict["build_finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ"),
                        datetime.strptime(build_results_dict["build_started_at"], "%Y-%m-%dT%H:%M:%S.%fZ")))
                else:
                    print(f'build_finished_at: {build_results_dict["build_finished_at"]}')
                    print(f'build_finished_at datetime: {datetime.strptime(build_results_dict["build_finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ")}')
                    build_results_dict["build_duration"] = "not finished"
                build_results_dict["test_branch"] = build["branch"]

                build_results_dict["cluster_era"] = DEFAULT_ERA
                build_results_dict["tx_era"] = DEFAULT_ERA
                if "CLUSTER_ERA" in build["pipeline"]["env"]:
                    build_results_dict["cluster_era"] = build["pipeline"]["env"]["CLUSTER_ERA"]
                if "TX_ERA" in build["pipeline"]["env"]:
                    build_results_dict["tx_era"] = build["pipeline"]["env"]["TX_ERA"]

                build_results_dict["node_branch"] = "master"
                if "dbsync" in env:
                    build_results_dict["dbsync_branch"] = "master"

                print(f"  ==== Writing test results into the {env} DB table for build no {build['number']}")
                build_results_dict = {k: "None" if v == "" else v for k, v in build_results_dict.items()}
                col_list = list(build_results_dict.keys())
                col_values = list(build_results_dict.values())

                if not add_single_value_into_db(table_name, col_list, col_values):
                    print(f"col_list  : {col_list}")
                    print(f"col_values: {col_values}")
                    exit(1)
            else:
                print(f" -- results for build {build['number']} are already into the DB")


if __name__ == "__main__":
    main()
