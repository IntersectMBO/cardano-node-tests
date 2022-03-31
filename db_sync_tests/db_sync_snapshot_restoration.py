import argparse
import json
import os
from os.path import normpath, basename
import platform
import random
import re
import signal
import subprocess
import mmap
import tarfile
import hashlib
import shutil
import gzip
import requests
import time
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from git import Repo

from psutil import process_iter
from utils import seconds_to_time, date_diff_in_seconds, get_no_of_cpu_cores, \
    get_current_date_time, get_os_type, get_directory_size, get_total_ram_in_GB, \
    upload_artifact, clone_repo, print_file, stop_process, export_env_var, create_dir, \
    zip_file, get_process_info, write_data_as_json_to_file, clone_repo_fork


ROOT_TEST_PATH = Path.cwd()

POSTGRES_DIR = ROOT_TEST_PATH.parents[0]
POSTGRES_USER = subprocess.run(['whoami'], stdout=subprocess.PIPE).stdout.decode('utf-8').strip()

db_sync_perf_stats = []
DB_SYNC_PERF_STATS_FILE_NAME = "db_sync_performance_stats.json"
DB_SYNC_PERF_STATS_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/{DB_SYNC_PERF_STATS_FILE_NAME}"

NODE_LOG_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-node/node_logfile.log"
DB_SYNC_LOG_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/db_sync_logfile.log"
TEST_RESULTS_FILE_NAME = 'test_results.json'
EPOCH_SYNC_TIMES_FILE_NAME = 'epoch_sync_times_dump.json'
EPOCH_SYNC_TIMES_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/{EPOCH_SYNC_TIMES_FILE_NAME}"

NODE_ARCHIVE = 'cardano_node.zip'
DB_SYNC_ARCHIVE = 'cardano_db_sync.zip'
SYNC_DATA_ARCHIVE = 'epoch_sync_times_dump.zip'
PERF_STATS_ARCHIVE = 'db_sync_perf_stats.zip'

ONE_MINUTE = 60

def get_environment():
    return vars(args)["environment"]


def get_node_pr():
    return str(vars(args)["node_pr"]).strip()


def get_node_branch():
    return str(vars(args)["node_branch"]).strip()


def get_node_version_from_gh_action():
    return str(vars(args)["node_version_gh_action"]).strip()


def get_db_sync_branch():
    return str(vars(args)["db_sync_branch"]).strip()


def get_db_sync_version_from_gh_action():
    return str(vars(args)["db_sync_version_gh_action"]).strip()


def get_snapshot_url():
    return str(vars(args)["snapshot_url"]).strip()


def get_log_output_frequency():
    env = vars(args)["environment"]
    if env == "mainnet":
        return 20
    else:
        return 10


def are_errors_present_in_logs(log_file):
    with open(log_file, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        if s.find(b'db-sync-node:Error') != -1:
            return "Yes"
        return "No"


def are_rollbacks_present_in_logs(log_file):
    with open(log_file, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        initial_rollback_position = s.find(b'Rolling')
        offset = s.find(b'Rolling', initial_rollback_position + len('Rolling'))
        if offset != -1:
            s.seek(offset)
            if s.find(b'Rolling'):
                return "Yes"
        return "No"


def print_n_last_lines_from_file(n, file_name):
    logs = subprocess.run(['tail', "-n", f"{n}", f"{file_name}"], stdout=subprocess.PIPE).stdout.decode('utf-8').strip().rstrip().splitlines()
    print("")
    for line in logs:
        print(line)
    print("")


def get_node_archive_url(node_pr):
    cardano_node_pr=f"-pr-{node_pr}"
    return f"https://hydra.iohk.io/job/Cardano/cardano-node{cardano_node_pr}/cardano-node-linux/latest-finished/download/1/"


def get_db_sync_archive_url(db_pr):
    cardano_db_sync_pr=f"-pr-{db_pr}"
    return f"https://hydra.iohk.io/job/Cardano/cardano-db-sync{cardano_db_sync_pr}/cardano-db-sync-linux/latest-finished/download/1/"


def download_snapshot(snapshot_url):
    current_directory = os.getcwd()
    headers = {'User-Agent': 'Mozilla/5.0'}
    archive_name = snapshot_url.split("/")[-1].strip()

    print("Download snapshot file:")
    print(f" - current_directory: {current_directory}")
    print(f" - download_url: {snapshot_url}")
    print(f" - archive name: {archive_name}")

    with requests.get(snapshot_url, headers = headers, stream = True) as r:
        r.raise_for_status()
        with open(archive_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return archive_name


def get_snapshot_sha_256_sum(snapshot_url):
    snapshot_sha_256_sum_url = snapshot_url + ".sha256sum"
    for line in requests.get(snapshot_sha_256_sum_url):
        return line.decode('utf-8').split(" ")[0]


def get_file_sha_256_sum(filename):
    with open(filename,"rb") as f:
        bytes = f.read()
        readable_hash = hashlib.sha256(bytes).hexdigest();
        return readable_hash


def get_and_extract_archive_files(archive_url):
    current_directory = os.getcwd()
    request = requests.get(archive_url, allow_redirects=True)
    download_url = request.url
    archive_name = download_url.split("/")[-1].strip()

    print("Get and extract archive files:")
    print(f" - current_directory: {current_directory}")
    print(f" - download_url: {download_url}")
    print(f" - archive name: {archive_name}")

    urllib.request.urlretrieve(download_url, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")
    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    print(f" ------ listdir (after archive extraction): {os.listdir(current_directory)}")


def emergency_upload_artifacts():
    stop_process('cardano-db-sync')
    stop_process('cardano-node')

    write_data_as_json_to_file(DB_SYNC_PERF_STATS_FILE_NAME, db_sync_perf_stats)
    export_epoch_sync_times_from_db(EPOCH_SYNC_TIMES_FILE_NAME)

    zip_file(PERF_STATS_ARCHIVE, DB_SYNC_PERF_STATS_FILE_PATH)
    zip_file(SYNC_DATA_ARCHIVE, EPOCH_SYNC_TIMES_FILE_PATH)
    zip_file(DB_SYNC_ARCHIVE, DB_SYNC_LOG_FILE_PATH)
    zip_file(NODE_ARCHIVE, NODE_LOG_FILE_PATH)

    upload_artifact(PERF_STATS_ARCHIVE)
    upload_artifact(SYNC_DATA_ARCHIVE)
    upload_artifact(DB_SYNC_ARCHIVE)
    upload_artifact(NODE_ARCHIVE)


def get_node_config_files(env):
    base_url = "https://hydra.iohk.io/job/Cardano/iohk-nix/cardano-deployment/latest-finished/download/1/"
    urllib.request.urlretrieve(base_url + env + "-config.json",env + "-config.json",)
    urllib.request.urlretrieve(base_url + env + "-byron-genesis.json", env + "-byron-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "-shelley-genesis.json", env + "-shelley-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "-alonzo-genesis.json", env + "-alonzo-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "-topology.json", env + "-topology.json",)


def set_node_socket_path_env_var_in_cwd():
    current_directory = Path.cwd()
    if not 'cardano-node' == basename(normpath(current_directory)):
        raise Exception(f"You're not inside 'cardano-node' directory but in: {current_directory}")
    socket_path = 'db/node.socket'
    export_env_var("CARDANO_NODE_SOCKET_PATH", socket_path)


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


def get_node_version():
    try:
        cmd = "./cardano-cli --version"
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


def get_node_tip(timeout_minutes=20):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    cmd = "./cardano-cli query tip " + get_testnet_value()

    for i in range(timeout_minutes):
        try:
            output = (
                subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8").strip()
            )
            output_json = json.loads(output)
            os.chdir(current_directory)
            if output_json["epoch"] is not None:
                output_json["epoch"] = int(output_json["epoch"])
            if "syncProgress" not in output_json:
                output_json["syncProgress"] = None
            else:
                output_json["syncProgress"] = float(output_json["syncProgress"])

            return output_json["epoch"], int(output_json["block"]), output_json["hash"], \
                   int(output_json["slot"]), output_json["era"].lower(), output_json["syncProgress"]
        except subprocess.CalledProcessError as e:
            print(f" === Waiting 60s before retrying to get the tip again - {i}")
            print(f"     !!!ERROR: command {e.cmd} return with error (code {e.returncode}): {' '.join(str(e.output).split())}")
            if "Invalid argument" in str(e.output):
                exit(1)
            pass
        time.sleep(ONE_MINUTE)
    exit(1)


def wait_for_node_to_start():
    # when starting from clean state it might take ~30 secs for the cli to work
    # when starting from existing state it might take >10 mins for the cli to work (opening db and
    # replaying the ledger)
    start_counter = time.perf_counter()
    get_node_tip(18000)
    stop_counter = time.perf_counter()

    start_time_seconds = int(stop_counter - start_counter)
    print(f" === It took {start_time_seconds} seconds for the QUERY TIP command to be available")
    return start_time_seconds


def start_node_in_cwd(env):
    current_directory = Path.cwd()
    if not 'cardano-node' == basename(normpath(current_directory)):
        raise Exception(f"You're not inside 'cardano-node' directory but in: {current_directory}")

    print(f"current_directory: {current_directory}")
    cmd = (
        f"./cardano-node run --topology {env}-topology.json --database-path "
        f"{Path(ROOT_TEST_PATH) / 'cardano-node' / 'db'} "
        f"--host-addr 0.0.0.0 --port 3000 --config "
        f"{env}-config.json --socket-path ./db/node.socket"
    )

    logfile = open(NODE_LOG_FILE_PATH, "w+")
    print(f"start node cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd.split(" "), stdout=logfile, stderr=logfile)
        print("waiting for db folder to be created")
        counter = 0
        timeout_counter = 5 * ONE_MINUTE
        while not os.path.isdir(current_directory / "db"):
            time.sleep(1)
            counter += 1
            if counter > timeout_counter:
                print(
                    f"ERROR: waited {timeout_counter} seconds and the DB folder was not created yet")
                exit(1)

        print(f"DB folder was created after {counter} seconds")
        secs_to_start = wait_for_node_to_start()
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(current_directory / 'db')}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def wait_for_node_to_sync():
    start_sync = time.perf_counter()
    *data, node_sync_progress = get_node_tip()
    counter = 0

    print("--- Waiting for Node to sync")
    while node_sync_progress < 99.9:
        if counter % 10 == 0:
            node_epoch_no, node_block_no, node_hash, node_slot, node_era, node_sync_progress = get_node_tip()
            print(f"node progress [%]: {node_sync_progress}, epoch: {node_epoch_no}, block: {node_block_no}, slot: {node_slot}, era: {node_era}")
        time.sleep(ONE_MINUTE)
        counter += 1

    end_sync = time.perf_counter()
    sync_time_seconds = int(end_sync - start_sync)
    return sync_time_seconds


def setup_postgres():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    export_env_var("POSTGRES_DIR", POSTGRES_DIR)
    export_env_var("PGHOST", 'localhost')
    export_env_var("PGUSER", POSTGRES_USER)
    export_env_var("PGPORT", '5432')

    try:
        cmd = f"./scripts/postgres-start.sh {POSTGRES_DIR} -k"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        print(f"Setup postgres script output: {output}")
        os.chdir(current_directory)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def create_pgpass_file():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    export_env_var("POSTGRES_DIR", POSTGRES_DIR)
    export_env_var("PGHOST", 'localhost')
    export_env_var("PGUSER", POSTGRES_USER)
    export_env_var("PGPORT", '5432')
    export_env_var("ENVIRONMENT", get_environment())

    print("Inside function create_pgpass_file")
    try:
        cmd = "./scripts/create_pgpass_file.sh"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        print(f"Create pgpass file script output: {output}")
        os.chdir(current_directory)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def start_db_sync():
    current_directory = os.getcwd()
    print(f"Current directory: {current_directory}")
    os.chdir(ROOT_TEST_PATH)
    print(f"ROOT_TEST_PATH: {ROOT_TEST_PATH}")
    export_env_var("ENVIRONMENT", get_environment())
    export_env_var("LOG_FILEPATH", DB_SYNC_LOG_FILE_PATH)
    cmd = "./scripts/start_database_after_snapshot_restoration.sh"
    p = subprocess.Popen(cmd)

    try:
        outs, errs = p.communicate(timeout=1200)
        print(outs)
        os.chdir(current_directory)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )

    not_found = True
    counter = 0

    while not_found:
        if counter > 10 * ONE_MINUTE:
            print(f"ERROR: waited {counter} seconds and the db-sync was not started")
            exit(1)

        for proc in process_iter():
            if "cardano-db-sync" in proc.name():
                print(f"db-sync process present: {proc}")
                not_found = False
                return
        print("Waiting for db-sync to start")
        counter += ONE_MINUTE
        time.sleep(ONE_MINUTE)


def get_db_sync_version():
    try:
        cmd = "db-sync-node/bin/cardano-db-sync --version"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        cardano_db_sync_version = output.split("git revision ")[0].strip()
        cardano_db_sync_git_revision = output.split("git revision ")[1].strip()
        return str(cardano_db_sync_version), str(cardano_db_sync_git_revision)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def get_db_sync_progress():
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{get_environment()}",  "-c", "select 100 * (extract (epoch from (max (time) at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) / (extract (epoch from (now () at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) as sync_percent from block ;" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    should_try = True
    counter = 0

    while should_try:
        try:
            outs, errs = p.communicate(timeout=5)
            progress_string = outs.decode("utf-8")
            db_sync_progress = round(float(progress_string), 2)
            return db_sync_progress
        except ValueError:
            if counter > 15:
                should_try = False
                emergency_upload_artifacts()
                raise
            print(f"db-sync progress unavailable, possible postgress failure. Output from psql: {progress_string}")
            counter += 1
            time.sleep(ONE_MINUTE)
        except TimeoutExpired as e:
            p.kill()
            raise RuntimeError(
                "command '{}' return with error (code {}): {}".format(
                    e.cmd, e.returncode, " ".join(str(e.output).split())
                )
            )


def get_db_sync_tip():
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{get_environment()}",  "-c", "select epoch_no, block_no, slot_no from block order by id desc limit 1;" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    should_try = True
    counter = 0

    while should_try:
        try:
            outs, errs = p.communicate(timeout=5)
            output_string = outs.decode("utf-8")
            epoch_no, block_no, slot_no = [e.strip() for e in outs.decode("utf-8").split("|")]
            return epoch_no, block_no, slot_no
        except ValueError as e:
            if counter > 15:
                should_try = False
                emergency_upload_artifacts()
                raise
            print(f"db-sync tip data unavailable, possible postgress failure. Output from psql: {output_string}")
            counter += 1
            time.sleep(ONE_MINUTE)

        except TimeoutExpired as e:
            p.kill()
            raise RuntimeError(
                "command '{}' return with error (code {}): {}".format(
                    e.cmd, e.returncode, " ".join(str(e.output).split())
                )
            )


def export_epoch_sync_times_from_db(file, snapshot_epoch_no):
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    p = subprocess.Popen(["psql", f"{get_environment()}", "-t", "-c", f"\o {file}", "-c", f"SELECT array_to_json(array_agg(epoch_sync_time), FALSE) FROM epoch_sync_time where no > {snapshot_epoch_no};" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        outs, errs = p.communicate(timeout=5)
        print(errs.decode("utf-8"))
        return outs.decode("utf-8")
    except (TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def get_total_db_size():
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{get_environment()}", "-c", f"SELECT pg_size_pretty( pg_database_size('{get_environment()}') );" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        outs, errs = p.communicate(timeout=5)
        print(errs.decode("utf-8"))
        return outs.decode("utf-8").rstrip().strip()
    except (TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def wait_for_db_to_sync():
    start_sync = time.perf_counter()
    last_rollback_time = time.perf_counter()
    db_sync_progress = get_db_sync_progress()
    buildkite_timeout_in_sec = int(os.getenv('BUILDKITE_TIMEOUT')) * 60
    counter = 0
    rollback_counter = 0
    db_sync_process = get_process_info('cardano-db-sync')
    log_frequency = get_log_output_frequency()

    print("--- Db sync monitoring")
    while db_sync_progress < 99.9:
        sync_time_in_sec = time.perf_counter() - start_sync
        if sync_time_in_sec + 5 * ONE_MINUTE > buildkite_timeout_in_sec:
            emergency_upload_artifacts()
            raise Exception('Emergency uploading artifacts before buid timeout exception...')
        if counter % 2 == 0:
            current_progress = get_db_sync_progress()
            if current_progress < db_sync_progress and db_sync_progress > 3:
                print(f"Progress decreasing - current progress: {current_progress} VS previous: {db_sync_progress}.")
                print("Possible rollback... Printing last 10 lines of log")
                print_n_last_lines_from_file(10, DB_SYNC_LOG_FILE_PATH)
                if time.perf_counter() - last_rollback_time > 10 * ONE_MINUTE:
                    print("Resetting previous rollback counter as there was no progress decrease for more than 10 minutes")
                    rollback_counter = 0
                last_rollback_time = time.perf_counter()
                rollback_counter += 1
                print(f"Rollback counter: {rollback_counter} out of 13")
            if rollback_counter > 12:
                print(f"Progress decreasing for {rollback_counter * counter} minutes.")
                print(f"Shutting down all services and emergency uploading artifacts")
                emergency_upload_artifacts()
                raise Exception('Rollback taking too long. Shutting down...')
        if counter % log_frequency == 0:
            node_epoch_no, node_block_no, node_hash, node_slot, node_era, node_sync_progress = get_node_tip()
            print(f"node progress [%]: {node_sync_progress}, epoch: {node_epoch_no}, block: {node_block_no}, slot: {node_slot}, era: {node_era}")
            epoch_no, block_no, slot_no = get_db_sync_tip()
            db_sync_progress = get_db_sync_progress()
            sync_time_h_m_s = seconds_to_time(time.perf_counter() - start_sync)
            print(f"db sync progress [%]: {db_sync_progress}, sync time [h:m:s]: {sync_time_h_m_s}, epoch: {epoch_no}, block: {block_no}, slot: {slot_no}")
            print_n_last_lines_from_file(5, DB_SYNC_LOG_FILE_PATH)

        try:
            time_point = int(time.perf_counter() - start_sync)
            _, _, slot_no = get_db_sync_tip()
            cpu_usage = db_sync_process.cpu_percent(interval=None)
            rss_mem_usage = db_sync_process.memory_info()[0]
        except:
            end_sync = time.perf_counter()
            db_full_sync_time_in_secs = int(end_sync - start_sync)
            print("Unexpected error during sync process")
            return db_full_sync_time_in_secs

        stats_data_point = {"time": time_point, "slot_no": slot_no, "cpu_percent_usage": cpu_usage, "rss_mem_usage": rss_mem_usage}
        db_sync_perf_stats.append(stats_data_point)
        time.sleep(ONE_MINUTE)
        counter += 1

    end_sync = time.perf_counter()
    sync_time_seconds = int(end_sync - start_sync)
    return sync_time_seconds


def restore_snapshot(snapshot_name):
    export_env_var("PGPASSFILE" , f"config/pgpass-{get_environment()}")
    ledger_dir = create_dir(f"ledger-state/{get_environment()}")
    start_restoration = time.perf_counter()

    p = subprocess.Popen(["scripts/postgresql-setup.sh", "--restore-snapshot", f"{snapshot_name}", f"{ledger_dir}"], stdout=subprocess.PIPE)
    try:
        outs, errs = p.communicate(timeout=7200)
        print(outs.decode("utf-8"))

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )
    except subprocess.TimeoutExpired as e:
        p.kill()
        print(e)

    if "All good!" not in outs.decode("utf-8"):
        raise RuntimeError("Restoration has not ended successfully")

    end_restoration = time.perf_counter()
    return int(end_restoration - start_restoration)


def main():

    platform_system, platform_release, platform_version = get_os_type()
    print(f"Platform: {platform_system, platform_release, platform_version}")

    start_test_time = get_current_date_time()
    print(f"Test start time: {start_test_time}")

    env = get_environment()
    print(f"Environment: {env}")

    node_pr = get_node_pr()
    print(f"Node PR number: {node_pr}")

    node_branch = get_node_branch()
    print(f"Node branch: {node_branch}")

    node_version_from_gh_action = get_node_version_from_gh_action()
    print(f"Node version: {node_version_from_gh_action}")

    db_branch = get_db_sync_branch()
    print(f"DB sync branch: {db_branch}")

    db_sync_version_from_gh_action = get_db_sync_version_from_gh_action()
    print(f"DB sync version: {db_sync_version_from_gh_action}")

    snapshot_url = get_snapshot_url()
    print(f"Snapshot url: {snapshot_url}")

    # cardano-node setup
    NODE_DIR=create_dir('cardano-node')
    os.chdir(NODE_DIR)
    set_node_socket_path_env_var_in_cwd()
    get_node_config_files(env)
    get_and_extract_archive_files(get_node_archive_url(node_pr))
    cli_version, cli_git_rev = get_node_version()
    start_node_in_cwd(env)
    print("--- Node startup")
    print_file(NODE_LOG_FILE_PATH, 80)
    node_sync_time_in_secs = wait_for_node_to_sync()

    # cardano-db sync setup
    print("--- Db sync setup")
    os.chdir(ROOT_TEST_PATH)
    DB_SYNC_DIR = clone_repo_fork('cardano-db-sync', db_branch)
    os.chdir(DB_SYNC_DIR)

    snapshot_name = download_snapshot(snapshot_url)
    expected_snapshot_sha_256_sum = get_snapshot_sha_256_sum(snapshot_url)
    actual_snapshot_sha_256_sum = get_file_sha_256_sum(snapshot_name)
    assert expected_snapshot_sha_256_sum == actual_snapshot_sha_256_sum, "Incorrect sha 256 sum"

    setup_postgres()
    create_pgpass_file()

    TMP_DIR=create_dir('tmp')
    export_env_var("TMPDIR", TMP_DIR)
    restoration_time = restore_snapshot(snapshot_name)
    print(f"Restoration time [sec]: {restoration_time}")
    snapshot_epoch_no, snapshot_block_no, snapshot_slot_no = get_db_sync_tip()
    print(f"db-sync tip: epoch: {snapshot_epoch_no}, block: {snapshot_block_no}, slot: {snapshot_slot_no}")
    os.chdir(DB_SYNC_DIR)
    export_env_var("TMPDIR", "/tmp")
    start_db_sync()
    db_sync_version, db_sync_git_rev = get_db_sync_version()
    print_file(DB_SYNC_LOG_FILE_PATH, 30)

    start_sync = time.perf_counter()
    db_full_sync_time_in_secs = wait_for_db_to_sync()

    end_test_time = get_current_date_time()
    epoch_no, block_no, slot_no = get_db_sync_tip()

    stop_process('cardano-db-sync')
    stop_process('cardano-node')

    print("--- Gathering end results")

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
    last_perf_stats_data_point = db_sync_perf_stats[-1]
    test_data["cpu_percent_usage"] = last_perf_stats_data_point["cpu_percent_usage"]
    test_data["total_rss_memory_usage_in_B"] = last_perf_stats_data_point["rss_mem_usage"]
    test_data["total_database_size"] = get_total_db_size()
    test_data["rollbacks"] = are_rollbacks_present_in_logs(DB_SYNC_LOG_FILE_PATH)
    test_data["errors"] = are_errors_present_in_logs(DB_SYNC_LOG_FILE_PATH)

    write_data_as_json_to_file(TEST_RESULTS_FILE_NAME, test_data)
    write_data_as_json_to_file(DB_SYNC_PERF_STATS_FILE_NAME, db_sync_perf_stats)

    export_epoch_sync_times_from_db(EPOCH_SYNC_TIMES_FILE_NAME, snapshot_epoch_no)

    print_file(TEST_RESULTS_FILE_NAME)

    # compress artifacts
    zip_file(NODE_ARCHIVE, NODE_LOG_FILE_PATH)
    zip_file(DB_SYNC_ARCHIVE, DB_SYNC_LOG_FILE_PATH)
    zip_file(SYNC_DATA_ARCHIVE, EPOCH_SYNC_TIMES_FILE_PATH)
    zip_file(PERF_STATS_ARCHIVE, DB_SYNC_PERF_STATS_FILE_PATH)

    # upload artifacts
    upload_artifact(NODE_ARCHIVE)
    upload_artifact(DB_SYNC_ARCHIVE)
    upload_artifact(SYNC_DATA_ARCHIVE)
    upload_artifact(PERF_STATS_ARCHIVE)
    upload_artifact(TEST_RESULTS_FILE_NAME)


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
