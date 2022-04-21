import argparse
import json
import os
import platform
import random
import re
import signal
import subprocess
import tarfile

import requests
import time
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from psutil import process_iter

from explorer_utils import get_epoch_start_datetime_from_explorer
from blockfrost_utils import get_epoch_start_datetime_from_blockfrost

from utils import seconds_to_time, date_diff_in_seconds, get_no_of_cpu_cores, \
    get_current_date_time, get_os_type, get_directory_size, get_total_ram_in_GB

NODE = "./cardano-node"
CLI = "./cardano-cli"
ROOT_TEST_PATH = ""
NODE_LOG_FILE = "logfile.log"
RESULTS_FILE_NAME = r"sync_results.json"


def set_repo_paths():
    global ROOT_TEST_PATH
    ROOT_TEST_PATH = Path.cwd()

    print(f"ROOT_TEST_PATH: {ROOT_TEST_PATH}")


def git_get_commit_sha_for_tag_no(tag_no):
    global jData
    url = "https://api.github.com/repos/input-output-hk/cardano-node/tags"
    response = requests.get(url)

    # there is a rate limit for the provided url that we want to overpass with the below loop
    count = 0
    while not response.ok:
        time.sleep(random.randint(30, 350))
        count += 1
        response = requests.get(url)
        if count > 15:
            print(
                f"!!!! ERROR: Could not get the commit sha for tag {tag_no} after {count} retries")
            response.raise_for_status()
    jData = json.loads(response.content)

    for tag in jData:
        if tag.get('name') == tag_no:
            return tag.get('commit').get('sha')

    print(f" ===== !!! ERROR: The specified tag_no - {tag_no} - was not found ===== ")
    print(json.dumps(jData, indent=4, sort_keys=True))
    return None


def git_get_hydra_eval_link_for_commit_sha(commit_sha):
    global jData
    url = f"https://api.github.com/repos/input-output-hk/cardano-node/commits/{commit_sha}/status"
    response = requests.get(url)

    # there is a rate limit for the provided url that we want to overpass with the below loop
    count = 0
    while not response.ok:
        time.sleep(random.randint(30, 240))
        count += 1
        response = requests.get(url)
        if count > 10:
            print(
                f"!!!! ERROR: Could not get the hydra eval link for tag {commit_sha} after {count} retries")
            response.raise_for_status()
    jData = json.loads(response.content)

    for status in jData.get('statuses'):
        if "hydra.iohk.io/eval" in status.get("target_url"):
            return status.get("target_url")

    print(
        f" ===== !!! ERROR: There is not eval link for the provided commit_sha - {commit_sha} =====")
    print(json.dumps(jData, indent=2, sort_keys=True))
    return None


def get_hydra_build_download_url(eval_url, os_type):
    global eval_jData, build_jData

    expected_os_types = ["windows", "linux", "macos"]
    if os_type not in expected_os_types:
        raise Exception(
            f" ===== !!! ERROR: provided os_type - {os_type} - not expected - {expected_os_types}")

    headers = {'Content-type': 'application/json'}
    eval_response = requests.get(eval_url, headers=headers)

    eval_jData = json.loads(eval_response.content)

    if eval_response.ok:
        eval_jData = json.loads(eval_response.content)
    else:
        eval_response.raise_for_status()

    for build_no in eval_jData.get("builds"):
        build_url = f"https://hydra.iohk.io/build/{build_no}"
        build_response = requests.get(build_url, headers=headers)

        count = 0
        while not build_response.ok:
            time.sleep(2)
            count += 1
            build_response = requests.get(build_url, headers=headers)
            if count > 9:
                build_response.raise_for_status()

        build_jData = json.loads(build_response.content)

        if os_type.lower() == "windows":
            if "cardano-node-win64" in build_jData.get("job"):
                job_name = build_jData.get('buildproducts').get('1').get('name')
                print(f"build_jData: {build_jData}")
                print(f"  -- cardano_node_pr: {build_jData.get('jobset')}")
                print(f"  -- job_name: {job_name}")
                return f"https://hydra.iohk.io/build/{build_no}/download/1/{job_name}"
        elif os_type.lower() == "linux":
            if "cardano-node-linux" in build_jData.get("job"):
                job_name = build_jData.get('buildproducts').get('1').get('name')
                print(f"build_jData: {build_jData}")
                print(f"  -- cardano_node_pr: {build_jData.get('jobset')}")
                print(f"  -- job_name: {job_name}")
                return f"https://hydra.iohk.io/build/{build_no}/download/1/{job_name}"
        elif os_type.lower() == "macos":
            if "cardano-node-macos" in build_jData.get("job"):
                job_name = build_jData.get('buildproducts').get('1').get('name')
                print(f"build_jData: {build_jData}")
                print(f"  -- cardano_node_pr: {build_jData.get('jobset')}")
                print(f"  -- job_name: {job_name}")
                return f"https://hydra.iohk.io/build/{build_no}/download/1/{job_name}"

    print(f" ===== !!! ERROR: No build has found for the required os_type - {os_type} - {eval_url}")
    return None


def check_string_format(input_string):
    if len(input_string) == 40:
        return "commit_sha_format"
    elif len(input_string) == 7:
        return "eval_url"
    else:
        return "tag_format"


def get_and_extract_node_files(tag_no):
    # sometimes we cannot identify the hydra eval no for a specific tag no ->
    # in such case we are starting the sync tests by specifying the hydra eval no
    # but also the tag_no (in order to add it in the database)
    print(" - get and extract the pre-built node files")
    current_directory = os.getcwd()
    print(f" - current_directory for extracting node files: {current_directory}")
    platform_system, platform_release, platform_version = get_os_type()

    if check_string_format(tag_no) == "tag_format":
        commit_sha = git_get_commit_sha_for_tag_no(tag_no)
    elif check_string_format(tag_no) == "commit_sha_format":
        commit_sha = tag_no
    elif check_string_format(tag_no) == "eval_url":
        commit_sha = None
    else:
        print(f" !!! ERROR: invalid format for tag_no - {tag_no}; Expected tag_no or commit_sha.")
        commit_sha = None

    if check_string_format(tag_no) == "eval_url":
        eval_no = tag_no
        eval_url = "https://hydra.iohk.io/eval/" + eval_no
    else:
        eval_url = git_get_hydra_eval_link_for_commit_sha(commit_sha)

    print(f"commit_sha  : {commit_sha}")
    print(f"eval_url    : {eval_url}")

    if "linux" in platform_system.lower():
        download_url = get_hydra_build_download_url(eval_url, "linux")
        get_and_extract_linux_files(download_url)
    elif "darwin" in platform_system.lower():
        download_url = get_hydra_build_download_url(eval_url, "macos")
        get_and_extract_macos_files(download_url)
    elif "windows" in platform_system.lower():
        download_url = get_hydra_build_download_url(eval_url, "windows")
        get_and_extract_windows_files(download_url)


def get_and_extract_linux_files(download_url):
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")
    archive_name = download_url.split("/")[-1].strip()

    print(f"archive_name: {archive_name}")
    print(f"download_url: {download_url}")

    urllib.request.urlretrieve(download_url, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")
    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    print(f" - listdir (after archive extraction): {os.listdir(current_directory)}")


def get_and_extract_macos_files(download_url):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")

    archive_name = download_url.split("/")[-1].strip()

    print(f"archive_name: {archive_name}")
    print(f"download_url: {download_url}")

    urllib.request.urlretrieve(download_url, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")
    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    print(f" - listdir (after archive extraction): {os.listdir(current_directory)}")


def get_and_extract_windows_files(download_url):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")

    archive_name = download_url.split("/")[-1].strip()

    print(f"archive_name: {archive_name}")
    print(f"download_url: {download_url}")

    urllib.request.urlretrieve(download_url, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")
    with zipfile.ZipFile(Path(current_directory) / archive_name, "r") as zip_ref:
        zip_ref.extractall(current_directory)
    print(f" ------ listdir (after archive extraction): {os.listdir(current_directory)}")


def delete_node_files():
    os.chdir(Path(ROOT_TEST_PATH))
    for p in Path(".").glob("cardano-*"):
        print(f" === deleting file: {p}")
        p.unlink(missing_ok=True)


def get_node_config_files(env):
    os.chdir(Path(ROOT_TEST_PATH))
    urllib.request.urlretrieve(
        "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
        + env
        + "-config.json",
        env + "-config.json",
    )
    urllib.request.urlretrieve(
        "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
        + env
        + "-byron-genesis.json",
        env + "-byron-genesis.json",
    )
    urllib.request.urlretrieve(
        "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
        + env
        + "-shelley-genesis.json",
        env + "-shelley-genesis.json",
    )
    urllib.request.urlretrieve(
        "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
        + env
        + "-alonzo-genesis.json",
        env + "-alonzo-genesis.json",
    )
    urllib.request.urlretrieve(
        "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
        + env
        + "-topology.json",
        env + "-topology.json",
    )


def enable_cardano_node_resources_monitoring(node_config_filepath):
    with open(node_config_filepath, "r") as json_file:
        node_config_json = json.load(json_file)

    node_config_json["options"]["mapBackends"]["cardano.node.resources"] = ["KatipBK"]
    # node_config_json["TestEnableDevelopmentNetworkProtocols"] = True

    with open(node_config_filepath, "w") as json_file:
        json.dump(node_config_json, json_file, indent=2)


def set_node_socket_path_env_var():
    if "windows" in platform.system().lower():
        socket_path = "\\\\.\pipe\cardano-node"
    else:
        socket_path = (Path(ROOT_TEST_PATH) / "db" / "node.socket").expanduser().absolute()

    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)


def get_epoch_no_d_zero():
    env = vars(args)["environment"]
    if env == "mainnet":
        return 257
    elif env == "testnet":
        return 121
    elif env == "staging":
        return None
    elif env == "shelley_qa":
        return 2554
    else:
        return None


def get_start_slot_no_d_zero():
    env = vars(args)["environment"]

    if env == "mainnet":
        return 25661009
    elif env == "testnet":
        return 21902400
    elif env == "staging":
        return None
    elif env == "shelley_qa":
        return 18375135
    else:
        return None


def get_testnet_value():
    env = vars(args)["environment"]
    if env == "mainnet":
        return "--mainnet"
    elif env == "testnet":
        return "--testnet-magic 1097911063"
    elif env == "staging":
        return "--testnet-magic 633343913"
    elif env == "shelley_qa":
        return "--testnet-magic 3"
    else:
        return None


def wait_for_node_to_start(tag_no):
    # when starting from clean state it might take ~30 secs for the cli to work
    # when starting from existing state it might take > 10 mins for the cli to work (opening db and
    # replaying the ledger)
    start_counter = time.perf_counter()
    get_current_tip(timeout_seconds=18000)
    stop_counter = time.perf_counter()

    start_time_seconds = int(stop_counter - start_counter)
    print(f" === It took {start_time_seconds} seconds for the QUERY TIP command to be available")
    return start_time_seconds


def get_current_tip(timeout_seconds=10):
    cmd = CLI + " query tip " + get_testnet_value()

    for i in range(timeout_seconds):
        try:
            output = (
                subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                    .decode("utf-8")
                    .strip()
            )
            output_json = json.loads(output)

            if output_json["epoch"] is not None:
                output_json["epoch"] = int(output_json["epoch"])
            if "syncProgress" not in output_json:
                output_json["syncProgress"] = None
            else:
                output_json["syncProgress"] = int(float(output_json["syncProgress"]))

            return output_json["epoch"], int(output_json["block"]), output_json["hash"], \
                   int(output_json["slot"]), output_json["era"].lower(), output_json["syncProgress"]
        except subprocess.CalledProcessError as e:
            print(f" === Waiting 60s before retrying to get the tip again - {i}")
            print(
                f"     !!!ERROR: command {e.cmd} return with error (code {e.returncode}): {' '.join(str(e.output).split())}")
            if "Invalid argument" in str(e.output):
                exit(1)
            pass
        time.sleep(60)
    exit(1)


def get_node_version():
    try:
        cmd = CLI + " --version"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        cardano_cli_version = output.split("git rev ")[0].strip()
        cardano_cli_git_rev = output.split("git rev ")[1].strip()
        return str(cardano_cli_version), str(cardano_cli_git_rev)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def start_node_windows(env, tag_no):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    cmd = \
        NODE + \
        " run --topology " + \
        env + \
        "-topology.json --database-path db --port 3000 --config " + \
        env + \
        "-config.json --socket-path \\\\.\pipe\cardano-node "
    logfile = open(NODE_LOG_FILE, "w+")
    print(f"cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.PIPE)
        print("waiting for db folder to be created")
        count = 0
        count_timeout = 299
        while not os.path.isdir(current_directory / "db"):
            time.sleep(1)
            count += 1
            if count > count_timeout:
                print(
                    f"!!! ERROR: waited {count_timeout} seconds and the DB folder was not created yet")
                exit(1)

        print(f"DB folder was created after {count} seconds")
        secs_to_start = wait_for_node_to_start(tag_no)
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(current_directory / 'db')}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, ' '.join(str(e.output).split())))


def start_node_unix(env, tag_no):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    cmd = (
        f"{NODE} run --topology {env}-topology.json --database-path "
        f"{Path(ROOT_TEST_PATH) / 'db'} "
        f"--host-addr 0.0.0.0 --port 3000 --config "
        f"{env}-config.json --socket-path ./db/node.socket"
    )

    logfile = open(NODE_LOG_FILE, "w+")
    print(f"start node cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd.split(" "), stdout=logfile, stderr=logfile)
        print("waiting for db folder to be created")
        count = 0
        count_timeout = 299
        while not os.path.isdir(current_directory / "db"):
            time.sleep(1)
            count += 1
            if count > count_timeout:
                print(
                    f"ERROR: waited {count_timeout} seconds and the DB folder was not created yet")
                exit(1)

        print(f"DB folder was created after {count} seconds")
        secs_to_start = wait_for_node_to_start(tag_no)
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(current_directory / 'db')}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def stop_node(platform_system):
    for proc in process_iter():
        if "cardano-node" in proc.name():
            print(f" --- Killing the `cardano-node` process - {proc}")
            if "windows" in platform_system.lower():
                proc.send_signal(signal.SIGTERM)
            else:
                proc.send_signal(signal.SIGINT)
    time.sleep(10)
    for proc in process_iter():
        if "cardano-node" in proc.name():
            print(f" !!! ERROR: `cardano-node` process is still active - {proc}")


def get_calculated_slot_no(env):
    current_time = datetime.utcnow()
    shelley_start_time = byron_start_time = current_time

    if env == "testnet":
        byron_start_time = datetime.strptime("2019-07-24 20:20:16", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-28 20:20:16", "%Y-%m-%d %H:%M:%S")
    elif env == "staging":
        byron_start_time = datetime.strptime("2017-09-26 18:23:33", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-08-01 18:23:33", "%Y-%m-%d %H:%M:%S")
    elif env == "mainnet":
        byron_start_time = datetime.strptime("2017-09-23 21:44:51", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-29 21:44:51", "%Y-%m-%d %H:%M:%S")
    elif env == "shelley_qa":
        byron_start_time = datetime.strptime("2020-08-17 13:00:00", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-08-17 17:00:00", "%Y-%m-%d %H:%M:%S")

    last_slot_no = int(date_diff_in_seconds(shelley_start_time, byron_start_time) / 20 +
                       date_diff_in_seconds(current_time, shelley_start_time))

    return last_slot_no


def wait_for_node_to_sync(env):
    era_details_dict = OrderedDict()
    epoch_details_dict = OrderedDict()

    actual_epoch, actual_block, actual_hash, actual_slot, actual_era, syncProgress = get_current_tip()
    last_slot_no = get_calculated_slot_no(env)
    start_sync = time.perf_counter()

    count = 0
    if syncProgress is not None:
        while syncProgress < 100:
            if count % 60 == 0:
                print(f"actual_era  : {actual_era} "
                      f" - actual_epoch: {actual_epoch} "
                      f" - actual_block: {actual_block} "
                      f" - actual_slot : {actual_slot} "
                      f" - syncProgress: {syncProgress}")
            if actual_era not in era_details_dict:
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                if env == "mainnet":
                    actual_era_start_time = get_epoch_start_datetime_from_blockfrost(actual_epoch)
                else:
                    actual_era_start_time = get_epoch_start_datetime_from_explorer(env, actual_epoch)
                actual_era_dict = {"start_epoch": actual_epoch,
                                   "start_time": actual_era_start_time,
                                   "start_sync_time": current_time}
                era_details_dict[actual_era] = actual_era_dict
            if actual_epoch not in epoch_details_dict:
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                actual_epoch_dict = {"start_sync_time": current_time}
                epoch_details_dict[actual_epoch] = actual_epoch_dict

            time.sleep(5)
            count += 1
            actual_epoch, actual_block, actual_hash, actual_slot, actual_era, syncProgress = get_current_tip()
    else:
        while actual_slot <= last_slot_no:
            if count % 60 == 0:
                print(f"actual_era  : {actual_era} "
                      f" - actual_epoch: {actual_epoch} "
                      f" - actual_block: {actual_block} "
                      f" - actual_slot : {actual_slot} "
                      f" - syncProgress: {syncProgress}")
            if actual_era not in era_details_dict:
                if actual_epoch is None:
                    # TODO: to remove this after 'tip' bug returning None/null will be fixed
                    # https://github.com/input-output-hk/cardano-node/issues/2568
                    actual_epoch = 1
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                if env == "mainnet":
                    actual_era_start_time = get_epoch_start_datetime_from_blockfrost(actual_epoch)
                else:
                    actual_era_start_time = get_epoch_start_datetime_from_explorer(env, actual_epoch)
                actual_era_dict = {"start_epoch": actual_epoch,
                                   "start_time": actual_era_start_time,
                                   "start_sync_time": current_time}
                era_details_dict[actual_era] = actual_era_dict
            if actual_epoch not in epoch_details_dict:
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                actual_epoch_dict = {"start_sync_time": current_time}
                epoch_details_dict[actual_epoch] = actual_epoch_dict

            time.sleep(1)
            count += 1
            actual_epoch, actual_block, actual_hash, actual_slot, actual_era, syncProgress = get_current_tip()

    end_sync = time.perf_counter()

    sync_time_seconds = int(end_sync - start_sync)
    print(f"sync_time_seconds: {sync_time_seconds}")

    os.chdir(Path(ROOT_TEST_PATH) / "db" / "immutable")
    chunk_files = sorted(os.listdir(os.getcwd()), key=os.path.getmtime)
    latest_chunk_no = chunk_files[-1].split(".")[0]
    os.chdir(Path(ROOT_TEST_PATH))
    print(f"Sync done!; latest_chunk_no: {latest_chunk_no}")

    # add "end_sync_time", "slots_in_era", "sync_duration_secs" and "sync_speed_sps" for each era;
    # for the last/current era, "end_sync_time" = current_utc_time / end_of_sync_time
    eras_list = list(era_details_dict.keys())
    for era in eras_list:
        if era == eras_list[-1]:
            end_sync_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            last_epoch = actual_epoch
        else:
            end_sync_time = era_details_dict[eras_list[eras_list.index(era) + 1]]["start_sync_time"]
            last_epoch = int(
                era_details_dict[eras_list[eras_list.index(era) + 1]]["start_epoch"]) - 1

        actual_era_dict = era_details_dict[era]
        actual_era_dict["last_epoch"] = last_epoch
        actual_era_dict["end_sync_time"] = end_sync_time

        no_of_epochs_in_era = int(last_epoch) - int(
            era_details_dict[eras_list[eras_list.index(era)]]["start_epoch"]) + 1
        actual_era_dict["slots_in_era"] = get_no_of_slots_in_era(env, era, no_of_epochs_in_era)

        actual_era_dict["sync_duration_secs"] = date_diff_in_seconds(
            datetime.strptime(end_sync_time, "%Y-%m-%dT%H:%M:%SZ"),
            datetime.strptime(actual_era_dict["start_sync_time"], "%Y-%m-%dT%H:%M:%SZ"))

        actual_era_dict["sync_speed_sps"] = int(
            actual_era_dict["slots_in_era"] / actual_era_dict["sync_duration_secs"])

        era_details_dict[era] = actual_era_dict

    # calculate and add "end_sync_time" and "sync_duration_secs" for each epoch;
    epoch_list = list(epoch_details_dict.keys())
    for epoch in epoch_list:
        if epoch == epoch_list[-1]:
            epoch_end_sync_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            epoch_end_sync_time = epoch_details_dict[epoch_list[epoch_list.index(epoch) + 1]][
                "start_sync_time"]
        actual_epoch_dict = epoch_details_dict[epoch]
        actual_epoch_dict["end_sync_time"] = epoch_end_sync_time
        actual_epoch_dict["sync_duration_secs"] = date_diff_in_seconds(
            datetime.strptime(epoch_end_sync_time, "%Y-%m-%dT%H:%M:%SZ"),
            datetime.strptime(actual_epoch_dict["start_sync_time"], "%Y-%m-%dT%H:%M:%SZ"))
        epoch_details_dict[epoch] = actual_epoch_dict

    return sync_time_seconds, last_slot_no, latest_chunk_no, era_details_dict, epoch_details_dict


def get_no_of_slots_in_era(env, era_name, no_of_epochs_in_era):
    slot_length_secs = 1
    epoch_length_slots = 432000

    if era_name.lower() == "byron":
        slot_length_secs = 20
    if env == "shelley_qa":
        epoch_length_slots = 7200

    epoch_length_secs = int(epoch_length_slots / slot_length_secs)

    return int(epoch_length_secs * no_of_epochs_in_era)


def get_data_from_logs(log_file):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    tip_details_dict = OrderedDict()
    ram_details_dict = OrderedDict()
    centi_cpu_dict = OrderedDict()
    cpu_details_dict = OrderedDict()
    logs_details_dict = OrderedDict()

    with open(log_file) as f:
        log_file_lines = [line.rstrip() for line in f]

    for line in log_file_lines:
        if "cardano.node.resources" in line:
            timestamp = re.findall(r'\d{4}-\d{2}-\d{2} \d{1,2}:\d{1,2}:\d{1,2}', line)[0]
            ram_value = re.findall(r'"Heap",Number [-+]?[\d]+\.?[\d]*[Ee](?:[-+]?[\d]+)?', line)
            if len(ram_value) > 0:
                ram_details_dict[timestamp] = ram_value[0].split(' ')[1]

            centi_cpu = re.findall(r'"CentiCpu",Number \d+\.\d+', line)
            if len(centi_cpu) > 0:
                centi_cpu_dict[timestamp] = centi_cpu[0].split(' ')[1]
        if "new tip" in line:
            timestamp = re.findall(r'\d{4}-\d{2}-\d{2} \d{1,2}:\d{1,2}:\d{1,2}', line)[0]
            slot_no = line.split(" at slot ")[1]
            tip_details_dict[timestamp] = slot_no

    no_of_cpu_cores = get_no_of_cpu_cores()
    timestamps_list = list(centi_cpu_dict.keys())
    for timestamp1 in timestamps_list[1:]:
        # %CPU = dValue / dt for 1 core
        previous_timestamp = datetime.strptime(
            timestamps_list[timestamps_list.index(timestamp1) - 1], "%Y-%m-%d %H:%M:%S")
        current_timestamp = datetime.strptime(timestamps_list[timestamps_list.index(timestamp1)],
                                              "%Y-%m-%d %H:%M:%S")
        previous_value = float(
            centi_cpu_dict[timestamps_list[timestamps_list.index(timestamp1) - 1]])
        current_value = float(centi_cpu_dict[timestamps_list[timestamps_list.index(timestamp1)]])
        cpu_load_percent = (current_value - previous_value) / date_diff_in_seconds(
            current_timestamp, previous_timestamp)
        cpu_details_dict[timestamp1] = cpu_load_percent / no_of_cpu_cores

    all_timestamps_list = set(list(tip_details_dict.keys()) + list(ram_details_dict.keys()) + list(
        cpu_details_dict.keys()))
    for timestamp2 in all_timestamps_list:
        if timestamp2 not in list(tip_details_dict.keys()):
            tip_details_dict[timestamp2] = ""
        if timestamp2 not in list(ram_details_dict.keys()):
            ram_details_dict[timestamp2] = ""
        if timestamp2 not in list(cpu_details_dict.keys()):
            cpu_details_dict[timestamp2] = ""

        logs_details_dict[timestamp2] = {
            "tip": tip_details_dict[timestamp2],
            "ram": ram_details_dict[timestamp2],
            "cpu": cpu_details_dict[timestamp2]
        }

    return logs_details_dict


def main():
    start_test_time = get_current_date_time()
    print(f"Start test time:            {start_test_time}")
    global NODE
    global CLI

    secs_to_start1, secs_to_start2 = 0, 0

    set_repo_paths()

    env = vars(args)["environment"]
    print(f"env: {env}")

    set_node_socket_path_env_var()

    tag_no1 = str(vars(args)["node_tag_no1"]).strip()
    tag_no2 = str(vars(args)["node_tag_no2"]).strip()
    hydra_eval_no1 = str(vars(args)["hydra_eval_no1"]).strip()
    hydra_eval_no2 = str(vars(args)["hydra_eval_no2"]).strip()
    print(f"node_tag_no1: {tag_no1}")
    print(f"node_tag_no2: {tag_no2}")
    print(f"hydra_eval_no1: {hydra_eval_no1}")
    print(f"hydra_eval_no2: {hydra_eval_no2}")

    platform_system, platform_release, platform_version = get_os_type()
    print(f"platform: {platform_system, platform_release, platform_version}")

    if "windows" in platform_system.lower():
        NODE = "cardano-node.exe"
        CLI = "cardano-cli.exe"

    get_node_config_files_time = get_current_date_time()
    print(f"Get node config files time: {get_node_config_files_time}")
    print("get the node config files")
    get_node_config_files(env)

    print("Enable 'cardano node resource' monitoring")
    enable_cardano_node_resources_monitoring(env + "-config.json")

    get_node_build_files_time = get_current_date_time()
    print(f"Get node build files time:  {get_node_build_files_time}")
    print("get the pre-built node files")
    if hydra_eval_no1 == "None":
        get_and_extract_node_files(tag_no1)
    else:
        get_and_extract_node_files(hydra_eval_no1)

    print("===================================================================================")
    print(f"====================== Start node sync test using tag_no1: {tag_no1} =============")
    print("===================================================================================")

    print(" --- node version ---")
    cli_version1, cli_git_rev1 = get_node_version()
    print(f"  - cardano_cli_version1: {cli_version1}")
    print(f"  - cardano_cli_git_rev1: {cli_git_rev1}")

    print(f"   ======================= Start node using tag_no1: {tag_no1} ====================")
    start_sync_time1 = get_current_date_time()
    if "linux" in platform_system.lower() or "darwin" in platform_system.lower():
        secs_to_start1 = start_node_unix(env, tag_no1)
    elif "windows" in platform_system.lower():
        secs_to_start1 = start_node_windows(env, tag_no1)

    print(" - waiting for the node to sync")
    sync_time_seconds1, last_slot_no1, latest_chunk_no1, era_details_dict1, epoch_details_dict1 = wait_for_node_to_sync(env)

    end_sync_time1 = get_current_date_time()
    print(f"secs_to_start1            : {secs_to_start1}")
    print(f"start_sync_time1          : {start_sync_time1}")
    print(f"end_sync_time1            : {end_sync_time1}")

    # we are interested in the node logs only for the main sync - using tag_no1
    test_values_dict = OrderedDict()
    print(" === Parse the node logs and get the relevant data")
    logs_details_dict = get_data_from_logs(NODE_LOG_FILE)
    test_values_dict["log_values"] = json.dumps(logs_details_dict)

    print(f"   ======================= Start node using tag_no2: {tag_no2} ====================")
    (cardano_cli_version2, cardano_cli_git_rev2, shelley_sync_time_seconds2, total_chunks2,
     latest_block_no2, latest_slot_no2, start_sync_time2, end_sync_time2, start_sync_time3,
     sync_time_after_restart_seconds, cli_version2, cli_git_rev2, last_slot_no2, latest_chunk_no2,
     sync_time_seconds2
     ) = (None, None, None, None, None, None, None, None, None, None, None, None, None, None, 0)
    if tag_no2 != "None":
        print(f"   =============== Stop node using tag_no1: {tag_no1} ======================")
        stop_node(platform_system)

        print("   ================ Delete the previous node files =======================")
        delete_node_files()

        print("==============================================================================")
        print(f"===================== Start sync using tag_no2: {tag_no2} ===================")
        print("==============================================================================")
        if hydra_eval_no2 == "None":
            get_and_extract_node_files(tag_no2)
        else:
            get_and_extract_node_files(hydra_eval_no2)

        print(" --- node version ---")
        cli_version2, cli_git_rev2 = get_node_version()
        print(f"  - cardano_cli_version2: {cli_version2}")
        print(f"  - cardano_cli_git_rev2: {cli_git_rev2}")
        print(f"   ================ Start node using tag_no2: {tag_no2} ====================")
        start_sync_time2 = get_current_date_time()
        if "linux" in platform_system.lower() or "darwin" in platform_system.lower():
            secs_to_start2 = start_node_unix(env, tag_no2)
        elif "windows" in platform_system.lower():
            secs_to_start2 = start_node_windows(env, tag_no2)

        print(f" - waiting for the node to sync - using tag_no2: {tag_no2}")
        sync_time_seconds2, last_slot_no2, latest_chunk_no2, era_details_dict2, epoch_details_dict2 = wait_for_node_to_sync(env)
        end_sync_time2 = get_current_date_time()

    chain_size = get_directory_size(Path(ROOT_TEST_PATH) / "db")

    # Add the test values into the local copy of the database (to be pushed into sync tests repo)
    print("Node sync test ended; Creating the `test_values_dict` dict with the test values")
    print("++++++++++++++++++++++++++++++++++++++++++++++")

    for era in era_details_dict1:
        print(f"  *** {era} --> {era_details_dict1[era]}")
        test_values_dict[str(era + "_start_time")] = era_details_dict1[era]["start_time"]
        test_values_dict[str(era + "_start_epoch")] = era_details_dict1[era]["start_epoch"]
        test_values_dict[str(era + "_slots_in_era")] = era_details_dict1[era]["slots_in_era"]
        test_values_dict[str(era + "_start_sync_time")] = era_details_dict1[era]["start_sync_time"]
        test_values_dict[str(era + "_end_sync_time")] = era_details_dict1[era]["end_sync_time"]
        test_values_dict[str(era + "_sync_duration_secs")] = era_details_dict1[era][
            "sync_duration_secs"]
        test_values_dict[str(era + "_sync_speed_sps")] = era_details_dict1[era]["sync_speed_sps"]

    print("++++++++++++++++++++++++++++++++++++++++++++++")
    epoch_details = OrderedDict()
    for epoch in epoch_details_dict1:
        print(f"{epoch} --> {epoch_details_dict1[epoch]}")
        epoch_details[epoch] = epoch_details_dict1[epoch]["sync_duration_secs"]
    print("++++++++++++++++++++++++++++++++++++++++++++++")

    test_values_dict["env"] = env
    test_values_dict["tag_no1"] = tag_no1
    test_values_dict["tag_no2"] = tag_no2
    test_values_dict["cli_version1"] = cli_version1
    test_values_dict["cli_version2"] = cli_version2
    test_values_dict["cli_git_rev1"] = cli_git_rev1
    test_values_dict["cli_git_rev2"] = cli_git_rev2
    test_values_dict["start_sync_time1"] = start_sync_time1
    test_values_dict["end_sync_time1"] = end_sync_time1
    test_values_dict["start_sync_time2"] = start_sync_time2
    test_values_dict["end_sync_time2"] = end_sync_time2
    test_values_dict["last_slot_no1"] = last_slot_no1
    test_values_dict["last_slot_no2"] = last_slot_no2
    test_values_dict["start_node_secs1"] = secs_to_start1
    test_values_dict["start_node_secs2"] = secs_to_start2
    test_values_dict["sync_time_seconds1"] = sync_time_seconds1
    test_values_dict["sync_time1"] = seconds_to_time(int(sync_time_seconds1))
    test_values_dict["sync_time_seconds2"] = sync_time_seconds2
    test_values_dict["sync_time2"] = seconds_to_time(int(sync_time_seconds2))
    test_values_dict["total_chunks1"] = latest_chunk_no1
    test_values_dict["total_chunks2"] = latest_chunk_no2
    test_values_dict["platform_system"] = platform_system
    test_values_dict["platform_release"] = platform_release
    test_values_dict["platform_version"] = platform_version
    test_values_dict["chain_size_bytes"] = chain_size
    test_values_dict["sync_duration_per_epoch"] = json.dumps(epoch_details)
    test_values_dict["eras_in_test"] = json.dumps(list(era_details_dict1.keys()))
    test_values_dict["no_of_cpu_cores"] = get_no_of_cpu_cores()
    test_values_dict["total_ram_in_GB"] = get_total_ram_in_GB()
    test_values_dict["epoch_no_d_zero"] = get_epoch_no_d_zero()
    test_values_dict["start_slot_no_d_zero"] = get_start_slot_no_d_zero()
    test_values_dict["hydra_eval_no1"] = hydra_eval_no1
    test_values_dict["hydra_eval_no2"] = hydra_eval_no2

    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f"Write the test values to the {current_directory / RESULTS_FILE_NAME} file")
    with open(RESULTS_FILE_NAME, 'w') as results_file:
        json.dump(test_values_dict, results_file, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute basic sync test\n\n")

    parser.add_argument(
        "-t1", "--node_tag_no1", help="node tag number1 - used for initial sync, from clean state"
    )
    parser.add_argument(
        "-t2", "--node_tag_no2", help="node tag number2 - used for final sync, from existing state"
    )
    parser.add_argument(
        "-e1", "--hydra_eval_no1", help="hydra_eval_no1 - used for initial sync, from clean state"
    )
    parser.add_argument(
        "-e2", "--hydra_eval_no2", help="hydra_eval_no2 - used for final sync, from existing state"
    )
    parser.add_argument(
        "-e", "--environment",
        help="the environment on which to run the tests - shelley_qa, testnet, staging or mainnet.",
    )

    args = parser.parse_args()

    main()
