import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import argparse
import requests

from utils import date_diff_in_seconds, seconds_to_time

ORG_SLUG = "input-output-hk"
PIPELINE_SLUG = "cardano-node-tests-nightly"
NIGHTLY_FILENAME = "nightly_build.json"


def get_pipeline_builds(BUILDKITE_TOKEN):
    url = f"https://api.buildkite.com/v2/organizations/{ORG_SLUG}/pipelines/{PIPELINE_SLUG}/builds"
    headers = {'Authorization': "Bearer " + BUILDKITE_TOKEN}
    print(f"url: {url}")
    response = requests.get(url, headers=headers)

    status_code = response.status_code
    if status_code == 200:
        return response.json()
    else:
        print(f"status_code: {status_code}")
        print(f"response   : {response.json()}")
        print(f"!!! ERROR: status_code =! 200 when getting the builds for {PIPELINE_SLUG} pipeline")
        exit(1)


def main():
    secret = vars(args)["secret"]

    print(f"Getting the results of the last CLI Nightly run")
    nightly_build_dict = OrderedDict()

    cli_nightly_pipeline_builds = get_pipeline_builds(secret)
    # print(json.dumps(builds[0], indent=2))

    nightly_build_dict["start_time"] = cli_nightly_pipeline_builds[0]["started_at"]
    nightly_build_dict["finished_at"] = cli_nightly_pipeline_builds[0]["finished_at"]
    nightly_build_dict["duration"] = seconds_to_time(date_diff_in_seconds(
        datetime.strptime(nightly_build_dict["finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ"),
        datetime.strptime(nightly_build_dict["start_time"], "%Y-%m-%dT%H:%M:%S.%fZ")))
    nightly_build_dict["branch"] = cli_nightly_pipeline_builds[0]["branch"]
    nightly_build_dict["commit_no"] = cli_nightly_pipeline_builds[0]["commit"]
    nightly_build_dict["status"] = cli_nightly_pipeline_builds[0]["state"]
    nightly_build_dict["link"] = cli_nightly_pipeline_builds[0]["web_url"]

    for el in nightly_build_dict:
        print(f"{el} --> {nightly_build_dict[el]}")

    current_directory = Path.cwd()
    json_files_path = Path(current_directory) / "sync_tests" / "csv_files" / NIGHTLY_FILENAME
    print(f"Exporting the Nightly results into json file - {json_files_path}")
    print(f"  -- json_files_path: {json_files_path}")

    with open(json_files_path, 'w+', encoding='utf-8') as file:
        json.dump(nightly_build_dict, file, ensure_ascii=False, indent=4)

    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get node cli nightly results\n\n")

    parser.add_argument(
        "-t", "--secret", help="buildkite secret"
    )

    args = parser.parse_args()

    main()