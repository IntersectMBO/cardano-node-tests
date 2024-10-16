import argparse
import json
import os
import platform
import random
import re
import shlex
import shutil
import signal
import fileinput
import subprocess

import requests
import time
import urllib.request
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from psutil import process_iter

from explorer_utils import get_epoch_start_datetime_from_explorer
from blockfrost_utils import get_epoch_start_datetime_from_blockfrost
from gitpython_utils import git_clone_iohk_repo, git_checkout

import utils
from utils import print_info, print_ok, print_warn, print_info_warn, print_error, seconds_to_time, date_diff_in_seconds, get_no_of_cpu_cores, \
    get_current_date_time, get_os_type, get_directory_size, get_total_ram_in_GB, delete_file, is_dir, \
    list_absolute_file_paths, print_file_content

CONFIGS_BASE_URL = "https://book.play.dev.cardano.org/environments"
NODE = './cardano-node'
CLI = './cardano-cli'
ROOT_TEST_PATH = ''
NODE_LOG_FILE = 'logfile.log'
NODE_LOG_FILE_ARTIFACT = 'node.log'
RESULTS_FILE_NAME = r'sync_results.json'
ONE_MINUTE = 60


def set_repo_paths():
    global ROOT_TEST_PATH
    ROOT_TEST_PATH = Path.cwd()
    print(f"ROOT_TEST_PATH: {ROOT_TEST_PATH}")


def execute_command(command):
    print_info(f"Execute command: {command}")
    try:
        cmd = shlex.split(command)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        outs, errors = process.communicate(timeout=7200)               
        if errors:
            print_warn(f"Warnings or Errors --> {errors}")
        if outs:  
            print_ok(f"Output of command: {command} --> {outs}")                    
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print_error(f"Command {command} returned exception: {e}")
        raise


def git_get_last_closed_pr_cardano_node():
    global jData
    url = f"https://api.github.com/repos/input-output-hk/cardano-node/pulls?state=closed"
    response = requests.get(url)

    # there is a rate limit for the provided url that we want to overpass with the below loop
    count = 0
    while not response.ok:
        time.sleep(random.randint(30, 240))
        count += 1
        response = requests.get(url)
        if count > 10:
            print_error(
                f"!!!! ERROR: Could not get the number of the last closed PR after {count} retries")
            response.raise_for_status()
    jData = json.loads(response.content)
    print(f" -- last closed PR no is: {jData[0].get('url').split('/pulls/')[1].strip()}")
    return jData[0].get('url').split("/pulls/")[1].strip()


def check_string_format(input_string):
    if len(input_string) > 38:
        return 'commit_sha_format'
    elif input_string.strip().isdigit():
        return 'eval_url'
    else:
        return 'tag_format'


def delete_node_files():
    for p in Path(".").glob("cardano-*"):
        print_info_warn(f"deleting file: {p}")
        p.unlink(missing_ok=True)


def update_config(file_name: str, updates: dict) -> None:
    # Load the current configuration from the JSON file
    with open(file_name, 'r') as file:
        config = json.load(file)

    # Update the config with the new values
    for key, value in updates.items():
        if key in config:
            print(f"Updating '{key}' from '{config[key]}' to '{value}'")
            config[key] = value
        else:
            print(f"Key '{key}' not found in the config, adding new key-value pair")
            config[key] = value

    # Write the updated configuration back to the JSON file
    with open(file_name, 'w') as file:
        json.dump(config, file, indent=4)
    print("Configuration updated successfully.")


def disable_p2p_node_config():
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f" - listdir current_directory: {os.listdir(current_directory)}")

    updates = {
        "EnableP2P": False,
        "PeerSharing": False
    }
    update_config('config.json', updates)


def rm_node_config_files() -> None:
    print_info_warn('Removing existing config files')
    os.chdir(Path(ROOT_TEST_PATH))
    for gen in Path(".").glob("*-genesis.json"):
        Path(gen).unlink(missing_ok=True)
    for f in ('config.json', 'topology.json'):
        Path(f).unlink(missing_ok=True)


def download_config_file(env: str, file_name: str, save_as: str = None) -> None:
    save_as = save_as or file_name
    url = f"{CONFIGS_BASE_URL}/{env}/{file_name}"
    print(f"Downloading {file_name} from {url} and saving as {save_as}...")
    urllib.request.urlretrieve(url, save_as)


def get_node_config_files(env, node_topology_type):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    download_config_file(env, 'config.json')
    download_config_file(env, 'byron-genesis.json')
    download_config_file(env, 'shelley-genesis.json')
    download_config_file(env, 'alonzo-genesis.json')
    download_config_file(env, 'conway-genesis.json')

    if env == 'mainnet' and node_topology_type == 'non-bootstrap-peers':
        download_config_file(env, 'topology-non-bootstrap-peers.json', save_as='topology.json')
    elif env == 'mainnet' and node_topology_type == 'legacy':
        download_config_file(env, 'topology-legacy.json', save_as='topology.json')
        disable_p2p_node_config()
    else:
        download_config_file(env, 'topology.json')
    print(f" - listdir current_directory: {os.listdir(current_directory)}")
    print_info_warn(" Config File Content: ")
    print_file_content('config.json')
    print_info_warn(" Topology File Content: ")
    print_file_content('topology.json')


def enable_cardano_node_resources_monitoring(node_config_filepath):
    print_warn('- Enable cardano node resource monitoring:')
    print_info('  node_config_json["options"]["mapBackends"]["cardano.node.resources"] = ["KatipBK"]')
    
    os.chdir(Path(ROOT_TEST_PATH))
    with open(node_config_filepath, 'r') as json_file:
        node_config_json = json.load(json_file)
    node_config_json['options']['mapBackends']['cardano.node.resources'] = ['KatipBK']
    with open(node_config_filepath, 'w') as json_file:
        json.dump(node_config_json, json_file, indent=2)


def enable_cardano_node_tracers(node_config_filepath):
    os.chdir(Path(ROOT_TEST_PATH))
    with open(node_config_filepath, 'r') as json_file:
        node_config_json = json.load(json_file)

    print_warn('- Enable tracer:')
    print_info('  Set minSeverity = Info')
    node_config_json['minSeverity'] = 'Info'
    # node_config_json["TestEnableDevelopmentNetworkProtocols"] = True
    # node_config_json["TestEnableDevelopmentHardForkEras"] = True

    # node_config_json["EnableP2P"] = False
    # node_config_json["TraceLocalMux"] = True
    # node_config_json["TraceLocalHandshake"] = True
    # node_config_json["TraceLocalErrorPolicy"] = True
    # node_config_json["TraceErrorPolicy"] = True
    # node_config_json["TurnOnLogging"] = False
    #
    # if "mapSeverity" not in node_config_json["options"]:
    #     node_config_json["options"]["mapSeverity"] = {}
    # node_config_json["options"]["mapSeverity"]["cardano.node.LocalMux"] = "Info"
    # node_config_json["options"]["mapSeverity"]["cardano.node.LocalHandshake"] = "Info"
    # node_config_json["options"]["mapSeverity"]["cardano.node.LocalErrorPolicy"] = "Debug"
    # node_config_json["options"]["mapSeverity"]["cardano.node.ErrorPolicy"] = "Debug"

    # print(json.dumps(node_config_json, indent=2))

    with open(node_config_filepath, 'w') as json_file:
        json.dump(node_config_json, json_file, indent=2)


def set_node_socket_path_env_var():
    if 'windows' in platform.system().lower():
        socket_path = "\\\\.\pipe\cardano-node"
    else:
        socket_path = (Path(ROOT_TEST_PATH) / 'db' / 'node.socket').expanduser().absolute()
    os.environ['CARDANO_NODE_SOCKET_PATH'] = str(socket_path)


def get_epoch_no_d_zero():
    env = vars(args)['environment']
    if env == 'mainnet':
        return 257
    elif env == 'testnet':
        return 121
    elif env == 'staging':
        return None
    elif env == 'shelley-qa':
        return 2554
    else:
        return None


def get_start_slot_no_d_zero():
    env = vars(args)['environment']
    if env == 'mainnet':
        return 25661009
    elif env == 'testnet':
        return 21902400
    elif env == 'staging':
        return None
    elif env == 'shelley-qa':
        return 18375135
    else:
        return None


def get_testnet_value():
    env = vars(args)['environment']
    if env == 'mainnet':
        return '--mainnet'
    elif env == 'testnet':
        return '--testnet-magic 1097911063'
    elif env == 'staging':
        return '--testnet-magic 633343913'
    elif env == 'shelley-qa':
        return '--testnet-magic 3'
    elif env == 'preview':
        return '--testnet-magic 2'
    elif env == 'preprod':
        return '--testnet-magic 1'
    else:
        return None


def wait_for_node_to_start(timeout_minutes=20):
    # when starting from clean state it might take ~30 secs for the cli to work
    # when starting from existing state it might take > 10 mins for the cli to work (opening db and
    # replaying the ledger)
    start_counter = time.perf_counter()
    get_current_tip(timeout_minutes)
    stop_counter = time.perf_counter()
    start_time_seconds = int(stop_counter - start_counter)
    print_ok(f"It took {start_time_seconds} seconds for the QUERY TIP command to be available")
    return start_time_seconds


def get_current_tip(timeout_minutes=10):
    cmd = CLI + ' query tip ' + get_testnet_value()

    for i in range(timeout_minutes):
        try:
            output = (
                subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode('utf-8')
                .strip()
            )
            output_json = json.loads(output)
            epoch = int(output_json.get('epoch', 0))
            block = int(output_json.get('block', 0))
            hash_value = output_json.get('hash', "")
            slot = int(output_json.get('slot', 0))
            era = output_json.get('era', "").lower()
            sync_progress = int(float(output_json.get('syncProgress', 0.0))) if 'syncProgress' in output_json else None

            return epoch, block, hash_value, slot, era, sync_progress
        except subprocess.CalledProcessError as e:
            print(f" === {get_current_date_time()} - Waiting 60s before retrying to get the tip again - {i}")
            print_error(f"     !!! ERROR: command {e.cmd} returned with error (code {e.returncode}): {' '.join(str(e.output.decode('utf-8')).split())}")
            if "Invalid argument" in str(e.output):
                print(f" -- exiting on - {e.output.decode('utf-8')}")
                exit(1)
            pass
        time.sleep(ONE_MINUTE)
    exit(1)


def get_node_version():
    try:
        cmd = CLI + ' --version'
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            .decode('utf-8')
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


def start_node(cardano_node, tag_no, node_start_arguments, timeout_minutes=400):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    start_args = ' '.join(node_start_arguments)
    if 'None' in start_args:
        start_args = ''

    if platform.system().lower() == 'windows':
        cmd = (
            f"{cardano_node} run --topology topology.json "
            f"--database-path db "
            f"--host-addr 0.0.0.0 "
            f"--port 3000 "
            f"--socket-path \\\\.\pipe\cardano-node "
            f"--config config.json {start_args}"
        ).strip()
    else:
        cmd = (
            f"{cardano_node} run --topology topology.json --database-path "
            f"{Path(ROOT_TEST_PATH) / 'db'} "
            f"--host-addr 0.0.0.0 --port 3000 --config "
            f"config.json --socket-path ./db/node.socket {start_args}"
        ).strip()

    logfile = open(NODE_LOG_FILE, "w+")
    print_info_warn(f"start node cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd.split(" "), stdout=logfile, stderr=logfile)
        print_info('waiting for db folder to be created')
        count = 0
        count_timeout = 299
        while not os.path.isdir(current_directory / 'db'):
            time.sleep(1)
            count += 1
            if count > count_timeout:
                print_error(
                    f"ERROR: waited {count_timeout} seconds and the DB folder was not created yet")
                exit(1)

        print_ok(f"DB folder was created after {count} seconds")
        secs_to_start = wait_for_node_to_start(timeout_minutes)
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
        if 'cardano-node' in proc.name():
            print_info_warn(f"Killing the `cardano-node` process - {proc}")
            if 'windows' in platform_system.lower():
                proc.send_signal(signal.SIGTERM)
            else:
                proc.send_signal(signal.SIGINT)
    time.sleep(20)
    for proc in process_iter():
        if 'cardano-node' in proc.name():
            print_error(f" !!! ERROR: `cardano-node` process is still active - {proc}")


def copy_log_file_artifact(old_name, new_name):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    execute_command(f"cp {old_name} {new_name}")
    print(f" - listdir current_directory: {os.listdir(current_directory)}")


def get_calculated_slot_no(env):
    current_time = datetime.utcnow()
    shelley_start_time = byron_start_time = current_time

    if env == 'testnet':
        byron_start_time = datetime.strptime("2019-07-24 20:20:16", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-28 20:20:16", "%Y-%m-%d %H:%M:%S")
    elif env == 'staging':
        byron_start_time = datetime.strptime("2017-09-26 18:23:33", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-08-01 18:23:33", "%Y-%m-%d %H:%M:%S")
    elif env == 'mainnet':
        byron_start_time = datetime.strptime("2017-09-23 21:44:51", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-29 21:44:51", "%Y-%m-%d %H:%M:%S")
    elif env == 'shelley-qa':
        byron_start_time = datetime.strptime("2020-08-17 13:00:00", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-08-17 17:00:00", "%Y-%m-%d %H:%M:%S")
    elif env == 'preprod':
        byron_start_time = datetime.strptime("2022-06-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2022-06-21 00:00:00", "%Y-%m-%d %H:%M:%S")
    elif env == 'preview':
        # this env was started directly in Alonzo
        byron_start_time = datetime.strptime("2022-08-09 00:00:00", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2022-08-09 00:00:00", "%Y-%m-%d %H:%M:%S")

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
                print_warn(f"{get_current_date_time()} - actual_era  : {actual_era} "
                      f" - actual_epoch: {actual_epoch} "
                      f" - actual_block: {actual_block} "
                      f" - actual_slot : {actual_slot} "
                      f" - syncProgress: {syncProgress}")
            if actual_era not in era_details_dict:
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                if env == 'mainnet':
                    actual_era_start_time = get_epoch_start_datetime_from_blockfrost(actual_epoch)
                else:
                    actual_era_start_time = get_epoch_start_datetime_from_explorer(env, actual_epoch)
                actual_era_dict = {'start_epoch': actual_epoch,
                                   'start_time': actual_era_start_time,
                                   'start_sync_time': current_time}
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
                print_warn(f"{get_current_date_time()} - actual_era  : {actual_era} "
                      f" - actual_epoch: {actual_epoch} "
                      f" - actual_block: {actual_block} "
                      f" - actual_slot : {actual_slot} "
                      f" - syncProgress: {syncProgress}")
            if actual_era not in era_details_dict:
                current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                if env == 'mainnet':
                    actual_era_start_time = get_epoch_start_datetime_from_blockfrost(actual_epoch)
                else:
                    actual_era_start_time = get_epoch_start_datetime_from_explorer(env, actual_epoch)
                actual_era_dict = {'start_epoch': actual_epoch,
                                   'start_time': actual_era_start_time,
                                   'start_sync_time': current_time}
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

    os.chdir(Path(ROOT_TEST_PATH) / 'db' / 'immutable')
    chunk_files = sorted(os.listdir(os.getcwd()), key=os.path.getmtime)
    latest_chunk_no = chunk_files[-1].split(".")[0]
    os.chdir(Path(ROOT_TEST_PATH))
    print_ok(f"Sync done!; latest_chunk_no: {latest_chunk_no}")

    # add "end_sync_time", "slots_in_era", "sync_duration_secs" and "sync_speed_sps" for each era;
    # for the last/current era, "end_sync_time" = current_utc_time / end_of_sync_time
    eras_list = list(era_details_dict.keys())
    for era in eras_list:
        if era == eras_list[-1]:
            end_sync_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            last_epoch = actual_epoch
        else:
            end_sync_time = era_details_dict[eras_list[eras_list.index(era) + 1]]['start_sync_time']
            last_epoch = int(
                era_details_dict[eras_list[eras_list.index(era) + 1]]['start_epoch']) - 1

        actual_era_dict = era_details_dict[era]
        actual_era_dict['last_epoch'] = last_epoch
        actual_era_dict['end_sync_time'] = end_sync_time

        no_of_epochs_in_era = int(last_epoch) - int(
            era_details_dict[eras_list[eras_list.index(era)]]['start_epoch']) + 1
        actual_era_dict['slots_in_era'] = get_no_of_slots_in_era(env, era, no_of_epochs_in_era)

        actual_era_dict['sync_duration_secs'] = date_diff_in_seconds(
            datetime.strptime(end_sync_time, "%Y-%m-%dT%H:%M:%SZ"),
            datetime.strptime(actual_era_dict['start_sync_time'], "%Y-%m-%dT%H:%M:%SZ"))

        actual_era_dict['sync_speed_sps'] = int(
            actual_era_dict['slots_in_era'] / actual_era_dict['sync_duration_secs'])

        era_details_dict[era] = actual_era_dict

    # calculate and add "end_sync_time" and "sync_duration_secs" for each epoch;
    epoch_list = list(epoch_details_dict.keys())
    for epoch in epoch_list:
        if epoch == epoch_list[-1]:
            epoch_end_sync_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            epoch_end_sync_time = epoch_details_dict[epoch_list[epoch_list.index(epoch) + 1]][
                'start_sync_time']
        actual_epoch_dict = epoch_details_dict[epoch]
        actual_epoch_dict['end_sync_time'] = epoch_end_sync_time
        actual_epoch_dict['sync_duration_secs'] = date_diff_in_seconds(
            datetime.strptime(epoch_end_sync_time, "%Y-%m-%dT%H:%M:%SZ"),
            datetime.strptime(actual_epoch_dict['start_sync_time'], "%Y-%m-%dT%H:%M:%SZ"))
        epoch_details_dict[epoch] = actual_epoch_dict
    return sync_time_seconds, last_slot_no, latest_chunk_no, era_details_dict, epoch_details_dict


def get_no_of_slots_in_era(env, era_name, no_of_epochs_in_era):
    slot_length_secs = 1
    epoch_length_slots = 432000

    if era_name.lower() == 'byron':
        slot_length_secs = 20
    if env == 'shelley-qa':
        epoch_length_slots = 7200
    if env == 'preview':
        epoch_length_slots = 86400

    epoch_length_secs = int(epoch_length_slots / slot_length_secs)
    return int(epoch_length_secs * no_of_epochs_in_era)


def get_data_from_logs(log_file):
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")

    tip_details_dict = OrderedDict()
    heap_ram_details_dict = OrderedDict()
    rss_ram_details_dict = OrderedDict()
    centi_cpu_dict = OrderedDict()
    cpu_details_dict = OrderedDict()
    logs_details_dict = OrderedDict()

    with open(log_file) as f:
        log_file_lines = [line.rstrip() for line in f]

    for line in log_file_lines:
        if 'cardano.node.resources' in line:
            timestamp = re.findall(r'\d{4}-\d{2}-\d{2} \d{1,2}:\d{1,2}:\d{1,2}', line)[0]
            heap_ram_value = re.findall(r'"Heap",Number [-+]?[\d]+\.?[\d]*[Ee](?:[-+]?[\d]+)?', line)
            rss_ram_value = re.findall(r'"RSS",Number [-+]?[\d]+\.?[\d]*[Ee](?:[-+]?[\d]+)?', line)
            if len(heap_ram_value) > 0:
                heap_ram_details_dict[timestamp] = heap_ram_value[0].split(' ')[1]
            if len(rss_ram_value) > 0:
                rss_ram_details_dict[timestamp] = rss_ram_value[0].split(' ')[1]

            centi_cpu = re.findall(r'"CentiCpu",Number \d+\.\d+', line)
            if len(centi_cpu) > 0:
                centi_cpu_dict[timestamp] = centi_cpu[0].split(' ')[1]
        if 'new tip' in line:
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

    all_timestamps_list = set(list(tip_details_dict.keys()) + list(heap_ram_details_dict.keys()) + 
        list(rss_ram_details_dict.keys()) + list(cpu_details_dict.keys()))
    for timestamp2 in all_timestamps_list:
        if timestamp2 not in list(tip_details_dict.keys()):
            tip_details_dict[timestamp2] = ''
        if timestamp2 not in list(heap_ram_details_dict.keys()):
            heap_ram_details_dict[timestamp2] = ''
        if timestamp2 not in list(rss_ram_details_dict.keys()):
            rss_ram_details_dict[timestamp2] = ''
        if timestamp2 not in list(cpu_details_dict.keys()):
            cpu_details_dict[timestamp2] = ''

        logs_details_dict[timestamp2] = {
            'tip': tip_details_dict[timestamp2],
            'heap_ram': heap_ram_details_dict[timestamp2],
            'rss_ram': rss_ram_details_dict[timestamp2],
            'cpu': cpu_details_dict[timestamp2]
        }

    return logs_details_dict


def get_cabal_build_files():
    node_build_files = list_absolute_file_paths('dist-newstyle/build')
    return node_build_files


def get_node_executable_path_built_with_cabal():
    for f in get_cabal_build_files():
        if "\\x\\cardano-node\\build\\" in f and 'cardano-node-tmp' not in f and 'autogen' not in f:
            print_info(f"Found node executable: {f}")
            global NODE   
            NODE = f;    
            return f


def get_cli_executable_path_built_with_cabal():
    for f in get_cabal_build_files():
        if "\\x\\cardano-cli\\build\\" in f and 'cardano-cli-tmp' not in f and 'autogen' not in f:
            print_info(f"Found node-cli executable: {f}")
            global CLI
            CLI = f 
            return f


def copy_node_executables(src_location, dst_location, build_mode):
    if build_mode == 'nix':
        node_binary_location = 'cardano-node-bin/bin/'
        cli_binary_location = 'cardano-cli-bin/bin/'

        os.chdir(Path(src_location) / node_binary_location)
        print_info('files permissions inside cardano-node-bin/bin folder:')
        subprocess.check_call(['ls', '-la'])

        os.chdir(Path(src_location) / cli_binary_location)
        print_info('files permissions inside cardano-cli-bin/bin folder:')
        subprocess.check_call(['ls', '-la'])
        os.chdir(Path(dst_location))

        try:
            shutil.copy2(Path(src_location) / node_binary_location / 'cardano-node',
                         Path(dst_location) / 'cardano-node')
        except Exception as e:
            print_error(f" !!! ERROR - could not copy the cardano-cli file - {e}")
            exit(1)
        try:
            shutil.copy2(Path(src_location) / cli_binary_location / 'cardano-cli',
                         Path(dst_location) / 'cardano-cli')
        except Exception as e:
            print_error(f" !!! ERROR - could not copy the cardano-cli file - {e}")
            exit(1)
        time.sleep(5)

    if build_mode == 'cabal':
        node_binary_location = get_node_executable_path_built_with_cabal()
        cli_binary_location = get_cli_executable_path_built_with_cabal()

        try:
            shutil.copy2(node_binary_location, Path(dst_location) / 'cardano-node')
        except Exception as e:
            print_error(f" !!! ERROR - could not copy the cardano-cli file - {e}")

        try:
            shutil.copy2(cli_binary_location, Path(dst_location) / 'cardano-cli')
        except Exception as e:
            print_error(f" !!! ERROR - could not copy the cardano-cli file - {e}")
        time.sleep(5)


def get_node_files(node_rev, repository=None, build_tool='nix'):
    test_directory = Path.cwd()
    repo = None
    print_info(f"test_directory: {test_directory}")
    print(f" - listdir test_directory: {os.listdir(test_directory)}")

    node_repo_name = 'cardano-node'
    node_repo_dir = test_directory / 'cardano_node_dir'

    cli_rev = 'e7e5a86'
    cli_repo_name = 'cardano-cli'
    cli_repo_dir = test_directory / 'cardano_cli_dir'

    if is_dir(node_repo_dir):
        repo = git_checkout(repository, node_rev)
    else:
        repo = git_clone_iohk_repo(node_repo_name, node_repo_dir, node_rev)

    if is_dir(cli_repo_dir):
        git_checkout(repository, cli_rev)
    else:
        git_clone_iohk_repo(cli_repo_name, cli_repo_dir, cli_rev)

    if build_tool == 'nix':
        os.chdir(node_repo_dir)
        Path('cardano-node-bin').unlink(missing_ok=True)
        Path('cardano-cli-bin').unlink(missing_ok=True)
        execute_command("nix build -v .#cardano-node -o cardano-node-bin")
        execute_command("nix build -v .#cardano-cli -o cardano-cli-bin")
        copy_node_executables(node_repo_dir, test_directory, "nix")

    elif build_tool == 'cabal':
        cabal_local_file = Path(test_directory) / 'sync_tests' / 'cabal.project.local'

        # Build cli
        os.chdir(cli_repo_dir)
        shutil.copy2(cabal_local_file, cli_repo_dir)
        print(f" - listdir cli_repo_dir: {os.listdir(cli_repo_dir)}")
        shutil.rmtree('dist-newstyle', ignore_errors=True)
        for line in fileinput.input("cabal.project", inplace=True):
            print(line.replace("tests: True", "tests: False"), end="")
        execute_command("cabal update")
        execute_command("cabal build cardano-cli")
        copy_node_executables(cli_repo_dir, test_directory, "cabal")
        git_checkout(repo, 'cabal.project')

        # Build node
        os.chdir(node_repo_dir)
        shutil.copy2(cabal_local_file, node_repo_dir)
        print(f" - listdir node_repo_dir: {os.listdir(node_repo_dir)}")
        shutil.rmtree('dist-newstyle', ignore_errors=True)
        for line in fileinput.input("cabal.project", inplace=True):
            print(line.replace("tests: True", "tests: False"), end="")
        execute_command("cabal update")
        execute_command("cabal build cardano-node")
        copy_node_executables(node_repo_dir, test_directory, "cabal")
        git_checkout(repo, 'cabal.project')

    os.chdir(test_directory)
    subprocess.check_call(['chmod', '+x', NODE])
    subprocess.check_call(['chmod', '+x', CLI])
    print_info("files permissions inside test folder:")
    subprocess.check_call(['ls', '-la'])
    return repo


def main():
    global NODE, CLI, repository
    secs_to_start1, secs_to_start2 = 0, 0
    set_repo_paths()
    set_node_socket_path_env_var()

    print('--- Test data information', flush=True)
    start_test_time = get_current_date_time()
    print_info(f"Test start time: {start_test_time}")
    print_warn('Test parameters:')
    env = vars(args)['environment']
    node_build_mode = str(vars(args)['build_mode']).strip()
    node_rev1 = str(vars(args)['node_rev1']).strip()
    node_rev2 = str(vars(args)['node_rev2']).strip()
    tag_no1 = str(vars(args)['tag_no1']).strip()
    tag_no2 = str(vars(args)['tag_no2']).strip()
    node_topology_type1 = str(vars(args)['node_topology1']).strip()
    node_topology_type2 = str(vars(args)['node_topology2']).strip()
    node_start_arguments1 = vars(args)['node_start_arguments1']
    node_start_arguments2 = vars(args)['node_start_arguments2']
    repository = None
    print(f"- env: {env}")
    print(f"- node_build_mode: {node_build_mode}")
    print(f"- tag_no1: {tag_no1}")
    print(f"- tag_no2: {tag_no2}")
    print(f"- node_rev1: {node_rev1}")
    print(f"- node_rev2: {node_rev2}")
    print(f"- node_topology_type1: {node_topology_type1}")
    print(f"- node_topology_type2: {node_topology_type2}")
    print(f"- node_start_arguments1: {node_start_arguments1}")
    print(f"- node_start_arguments2: {node_start_arguments2}")

    platform_system, platform_release, platform_version = get_os_type()
    print(f"- platform: {platform_system, platform_release, platform_version}")

    print('--- Get the cardano-node files', flush=True)
    print_info(f"Get the cardano-node and cardano-cli files using - {node_build_mode}")
    start_build_time = get_current_date_time()
    if 'windows' not in platform_system.lower():
        repository = get_node_files(node_rev1)
    elif 'windows' in platform_system.lower():
        repository = get_node_files(node_rev1, build_tool='cabal')
    else:
        print_error(
            f"ERROR: method not implemented yet!!! Only building with NIX is supported at this moment - {node_build_mode}")
        exit(1)
    end_build_time = get_current_date_time()
    print_info(f"  - start_build_time: {start_build_time}")
    print_info(f"  - end_build_time: {end_build_time}")

    print_warn('--- node version ')
    cli_version1, cli_git_rev1 = get_node_version()
    print(f"  - cardano_cli_version1: {cli_version1}")
    print(f"  - cardano_cli_git_rev1: {cli_git_rev1}")

    print('--- Get the node configuration files')
    rm_node_config_files()
    # TO DO: change the default to P2P when full P2P will be supported on Mainnet
    get_node_config_files(env, node_topology_type1)

    print('Enabling the desired cardano node tracers')
    enable_cardano_node_resources_monitoring('config.json')
    enable_cardano_node_tracers('config.json')

    print(f"--- Start node sync test using node_rev1: {node_rev1}")
    print_ok("===================================================================================")
    print_ok(f"================== Start node sync test using node_rev1: {node_rev1} =============")
    print_ok("===================================================================================")
    print('')
    start_sync_time1 = get_current_date_time()
    secs_to_start1 = start_node(NODE, tag_no1, node_start_arguments1, timeout_minutes=10)

    print_info(' - waiting for the node to sync')
    sync_time_seconds1, last_slot_no1, latest_chunk_no1, era_details_dict1, epoch_details_dict1 = wait_for_node_to_sync(
        env)

    end_sync_time1 = get_current_date_time()
    print(f"secs_to_start1: {secs_to_start1}")
    print(f"start_sync_time1: {start_sync_time1}")
    print(f"end_sync_time1: {end_sync_time1}")
    print_warn(f"Stop node for: {node_rev1}")
    stop_node(platform_system)
    stop_node(platform_system)

    # we are interested in the node logs only for the main sync - using tag_no1
    test_values_dict = OrderedDict()
    print('--- Parse the node logs and get the relevant data')
    logs_details_dict = get_data_from_logs(NODE_LOG_FILE)
    test_values_dict['log_values'] = json.dumps(logs_details_dict)

    print(f"--- Start node using tag_no2: {tag_no2}")
    (cardano_cli_version2, cardano_cli_git_rev2, shelley_sync_time_seconds2, total_chunks2,
     latest_block_no2, latest_slot_no2, start_sync_time2, end_sync_time2, start_sync_time3,
     sync_time_after_restart_seconds, cli_version2, cli_git_rev2, last_slot_no2, latest_chunk_no2,
     sync_time_seconds2
     ) = (None, None, None, None, None, None, None, None, None, None, None, None, None, None, 0)
    if tag_no2 != 'None':
        delete_node_files()
        print('')
        print_ok("==============================================================================")
        print_ok(f"================= Start sync using node_rev2: {node_rev2} ===================")
        print_ok("==============================================================================")
        
        print('Get the cardano-node and cardano-cli files')
        if 'windows' not in platform_system.lower():
            get_node_files(node_rev2, repository)
        elif 'windows' in platform_system.lower():
            get_node_files(node_rev2, repository, build_tool='cabal')
        else:
            print_error(
                f"ERROR: method not implemented yet!!! Only building with NIX is supported at this moment - {node_build_mode}")
            exit(1)

        if env == 'mainnet' and (node_topology_type1 != node_topology_type2):
            print_warn('remove the previous topology')
            delete_file(Path(ROOT_TEST_PATH) / 'topology.json')
            print('Getting the node configuration files')
            get_node_config_files(env, node_topology_type2)

        print_warn('node version')
        cli_version2, cli_git_rev2 = get_node_version()
        print(f" - cardano_cli_version2: {cli_version2}")
        print(f" - cardano_cli_git_rev2: {cli_git_rev2}")
        print('')
        print(f"================ Start node using node_rev2: {node_rev2} ====================")
        start_sync_time2 = get_current_date_time()
        secs_to_start2 = start_node(NODE, tag_no2, node_start_arguments2)


        print_info(f" - waiting for the node to sync - using node_rev2: {node_rev2}")
        sync_time_seconds2, last_slot_no2, latest_chunk_no2, era_details_dict2, epoch_details_dict2 = wait_for_node_to_sync(
            env)
        end_sync_time2 = get_current_date_time()
        print_warn(f"Stop node for: {node_rev2}")
        stop_node(platform_system)
        stop_node(platform_system)

    chain_size = get_directory_size(Path(ROOT_TEST_PATH) / 'db')

    print('--- Node sync test completed')
    print('Node sync test ended; Creating the `test_values_dict` dict with the test values')
    print('++++++++++++++++++++++++++++++++++++++++++++++')
    for era in era_details_dict1:
        print(f"  *** {era} --> {era_details_dict1[era]}")
        test_values_dict[str(era + "_start_time")] = era_details_dict1[era]['start_time']
        test_values_dict[str(era + "_start_epoch")] = era_details_dict1[era]['start_epoch']
        test_values_dict[str(era + "_slots_in_era")] = era_details_dict1[era]['slots_in_era']
        test_values_dict[str(era + "_start_sync_time")] = era_details_dict1[era]['start_sync_time']
        test_values_dict[str(era + "_end_sync_time")] = era_details_dict1[era]['end_sync_time']
        test_values_dict[str(era + '_sync_duration_secs')] = era_details_dict1[era][
            'sync_duration_secs']
        test_values_dict[str(era + "_sync_speed_sps")] = era_details_dict1[era]['sync_speed_sps']
    print('++++++++++++++++++++++++++++++++++++++++++++++')
    epoch_details = OrderedDict()
    for epoch in epoch_details_dict1:
        print(f"{epoch} --> {epoch_details_dict1[epoch]}")
        epoch_details[epoch] = epoch_details_dict1[epoch]['sync_duration_secs']
    print('++++++++++++++++++++++++++++++++++++++++++++++')
    test_values_dict['env'] = env
    test_values_dict['tag_no1'] = tag_no1
    test_values_dict['tag_no2'] = tag_no2
    test_values_dict['cli_version1'] = cli_version1
    test_values_dict['cli_version2'] = cli_version2
    test_values_dict['cli_git_rev1'] = cli_git_rev1
    test_values_dict['cli_git_rev2'] = cli_git_rev2
    test_values_dict['start_sync_time1'] = start_sync_time1
    test_values_dict['end_sync_time1'] = end_sync_time1
    test_values_dict['start_sync_time2'] = start_sync_time2
    test_values_dict['end_sync_time2'] = end_sync_time2
    test_values_dict['last_slot_no1'] = last_slot_no1
    test_values_dict['last_slot_no2'] = last_slot_no2
    test_values_dict['start_node_secs1'] = secs_to_start1
    test_values_dict['start_node_secs2'] = secs_to_start2
    test_values_dict['sync_time_seconds1'] = sync_time_seconds1
    test_values_dict['sync_time1'] = seconds_to_time(int(sync_time_seconds1))
    test_values_dict['sync_time_seconds2'] = sync_time_seconds2
    test_values_dict['sync_time2'] = seconds_to_time(int(sync_time_seconds2))
    test_values_dict['total_chunks1'] = latest_chunk_no1
    test_values_dict['total_chunks2'] = latest_chunk_no2
    test_values_dict['platform_system'] = platform_system
    test_values_dict['platform_release'] = platform_release
    test_values_dict['platform_version'] = platform_version
    test_values_dict['chain_size_bytes'] = chain_size
    test_values_dict['sync_duration_per_epoch'] = json.dumps(epoch_details)
    test_values_dict['eras_in_test'] = json.dumps(list(era_details_dict1.keys()))
    test_values_dict['no_of_cpu_cores'] = get_no_of_cpu_cores()
    test_values_dict['total_ram_in_GB'] = get_total_ram_in_GB()
    test_values_dict['epoch_no_d_zero'] = get_epoch_no_d_zero()
    test_values_dict['start_slot_no_d_zero'] = get_start_slot_no_d_zero()
    test_values_dict['hydra_eval_no1'] = node_rev1
    test_values_dict['hydra_eval_no2'] = node_rev2

    print('--- Write tests results to file')
    os.chdir(Path(ROOT_TEST_PATH))
    current_directory = Path.cwd()
    print(f"current_directory: {current_directory}")
    print(f"Write the test values to the {current_directory / RESULTS_FILE_NAME} file")
    with open(RESULTS_FILE_NAME, 'w') as results_file:
        json.dump(test_values_dict, results_file, indent=2)

    print('--- Copy the node logs')
    # sometimes uploading the artifacts fails because the node still writes into
    # the log file during the upload even though an attepmt to stop it was made
    copy_log_file_artifact(NODE_LOG_FILE, NODE_LOG_FILE_ARTIFACT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Cardano Node sync test\n\n")

    parser.add_argument(
        "-b", "--build_mode", help="how to get the node files - nix, cabal, prebuilt"
    )
    parser.add_argument(
        "-e", "--environment",
        help="the environment on which to run the sync test - shelley-qa, preview, preprod, mainnet",
    )
    parser.add_argument(
        "-r1", "--node_rev1",
        help="desired cardano-node revision - cardano-node tag or branch (used for initial sync, from clean state)",
    )
    parser.add_argument(
        "-r2", "--node_rev2",
        help="desired cardano-node revision - cardano-node tag or branch (used for final sync, from existing state)",
    )
    parser.add_argument(
        "-t1", "--tag_no1", help="tag_no1 label as it will be shown in the db/visuals",
    )
    parser.add_argument(
        "-t2", "--tag_no2", help="tag_no2 label as it will be shown in the db/visuals",
    )
    parser.add_argument(
        "-n1", "--node_topology1", help="type of node topology used for the initial sync - legacy, non-bootstrap-peers, bootstrap-peers"
    )
    parser.add_argument(
        "-n2", "--node_topology2", help="type of node topology used for final sync (after restart) - legacy, non-bootstrap-peers, bootstrap-peers"
    )
    parser.add_argument(
        "-a1", "--node_start_arguments1", nargs='+', type=str,
        help="arguments to be passed when starting the node from clean state (first tag_no)",
    )
    parser.add_argument(
        "-a2", "--node_start_arguments2", nargs='+', type=str,
        help="arguments to be passed when starting the node from existing state (second tag_no)",
    )

    args = parser.parse_args()

    main()
