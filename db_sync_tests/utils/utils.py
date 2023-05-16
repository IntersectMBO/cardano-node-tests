import hashlib
import os
import platform
import shutil
import mmap
import zipfile
import signal
import subprocess
import requests
import urllib.request
import tarfile
import xmltodict
import json
import shlex
import psycopg2
from assertpy import assert_that, assert_warn

from os.path import normpath, basename
from pathlib import Path
from psutil import process_iter
from datetime import datetime
from git import Repo

import psutil
import time


ONE_MINUTE = 60
ROOT_TEST_PATH = Path.cwd()
ENVIRONMENT = os.environ['environment']

NODE_PR = os.environ['node_pr']
NODE_BRANCH = os.environ['node_branch']
NODE_VERSION = os.environ['node_version']

DB_SYNC_BRANCH = os.environ['db_sync_branch']
DB_SYNC_VERSION = os.environ['db_sync_version']

POSTGRES_DIR = ROOT_TEST_PATH.parents[0]
POSTGRES_USER = subprocess.run(['whoami'], stdout=subprocess.PIPE).stdout.decode('utf-8').strip()

db_sync_perf_stats = []
DB_SYNC_PERF_STATS = f"{ROOT_TEST_PATH}/cardano-db-sync/db_sync_{ENVIRONMENT}_performance_stats.json"

NODE_LOG = f"{ROOT_TEST_PATH}/cardano-node/node_{ENVIRONMENT}_logfile.log"
DB_SYNC_LOG = f"{ROOT_TEST_PATH}/cardano-db-sync/db_sync_{ENVIRONMENT}_logfile.log"
EPOCH_SYNC_TIMES = f"{ROOT_TEST_PATH}/cardano-db-sync/epoch_sync_times_{ENVIRONMENT}_dump.json"

NODE_ARCHIVE = f"cardano_node_{ENVIRONMENT}_logs.zip"
DB_SYNC_ARCHIVE = f"cardano_db_sync_{ENVIRONMENT}_logs.zip"
SYNC_DATA_ARCHIVE = f"epoch_sync_times_{ENVIRONMENT}_dump.zip"
PERF_STATS_ARCHIVE = f"db_sync_{ENVIRONMENT}_perf_stats.zip"


class sh_colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_color_log(log_type, message):
    print(f"{log_type}{message}{sh_colors.ENDC}")


def date_diff_in_seconds(dt2, dt1):
    # dt1 and dt2 should be datetime types
    timedelta = dt2 - dt1
    return int(timedelta.days * 24 * 3600 + timedelta.seconds)


def seconds_to_time(seconds_val):
    mins, secs = divmod(seconds_val, 60)
    hour, mins = divmod(mins, 60)
    return "%d:%02d:%02d" % (hour, mins, secs)


def get_os_type():
    return [platform.system(), platform.release(), platform.version()]


def get_no_of_cpu_cores():
    return os.cpu_count()


def get_total_ram_in_GB():
    return int(psutil.virtual_memory().total / 1000000000)


def get_machine_name():
    return platform.node()


def export_env_var(name, value):
    os.environ[name] = str(value)

    
def read_env_var(name):
    return os.environ[name]


def wait(seconds):
    time.sleep(seconds)


def clone_repo(repo_name, repo_branch):
    location = os.getcwd() + f"/{repo_name}"
    repo = Repo.clone_from(f"https://github.com/input-output-hk/{repo_name}.git", location)
    repo.git.checkout(repo_branch)
    print(f"Repo: {repo_name} cloned to: {location}")
    return location


def make_tarfile(output_filename, source_dir):
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))

       
def upload_artifact(file):
    path=f"{ENVIRONMENT}/{DB_SYNC_BRANCH}/"  
    cmd = ['which', 'buildkite-agent']
    p = subprocess.run(cmd, stdout=subprocess.PIPE).stdout.decode('utf-8').strip()

    if ('buildkite-agent' in p):
        _upload_buildkite_artifact(file)
        return
    if (p == ''):
        _upload_to_S3_bucket(file, path)


def _upload_to_S3_bucket(file, path, expected_file_size_limit_in_mb=20):
    file_size_in_mb = get_file_size(file)    
    this_machine = get_machine_name()
    slow_machines = [ 'workstation', 'actina' ]
    
    if this_machine in slow_machines and file_size_in_mb > expected_file_size_limit_in_mb:
        print(f"This machine has very slow network upload speed - skipping file {file} upload.")
        print(f"File has {file_size_in_mb} [MB]. Max file size limit for upload is set to {expected_file_size_limit_in_mb} [MB]")
        return
    
    try:
        cmd = ["aws", "s3", "cp", f"{file}", f"s3://cardano-qa-bucket/{path}"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
        outs, errs = p.communicate(timeout=1200)
        if errs:
            print(f"Error occured during {file} upload to S3: {errs}")
        if outs is not None: print(f"Output from {file} upload to S3: {outs}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' returned with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )
    except subprocess.TimeoutExpired as e:
        p.kill()
        print(f"TimeoutExpired exception occured during {file} upload to S3: {e}")
        

def _upload_buildkite_artifact(file):
    try:
        cmd = ["buildkite-agent", "artifact", "upload", f"{file}"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
        outs, errs = p.communicate(timeout=1200)
        if errs:
            print(f"Error occured during {file} upload to BuildKite: {errs}")
            p.kill()
        if outs is not None: print(f"Output from {file} upload to BuildKite: {outs}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' returned with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )
    except subprocess.TimeoutExpired as e:
        p.kill()
        print(f"TimeoutExpired exception occured during {file} upload to BuildKite: {e}")
              
              
def create_node_database_archive(env):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-node')
    node_directory = os.getcwd()
    node_db_archive = f"node-db-{env}.tar.gz"
    make_tarfile(node_db_archive, "db")
    os.chdir(current_directory)
    node_db_archive_path = node_directory + f"/{node_db_archive}"
    return node_db_archive_path


def set_github_env_var(env_var, value):
    env_file = os.getenv('GITHUB_ENV')
    with open(env_file, "a") as my_env_file:
        my_env_file.write(f"{env_var}={value}")


def set_github_job_summary(value):
    job_summary = os.getenv('GITHUB_STEP_SUMMARY')
    with open(job_summary, "a") as job_summary:
        job_summary.write(f"{value}")
        job_summary.write(f"\n\n")


def set_github_warning(warning_msg):
  print(f"::warning::{warning_msg}")

        
def set_buildkite_meta_data(key, value):
    p = subprocess.Popen(["buildkite-agent", "meta-data", "set", f"{key}", f"{value}"])
    outs, errs = p.communicate(timeout=15)


def get_buildkite_meta_data(key):
    p = subprocess.Popen(["buildkite-agent", "meta-data", "get", f"{key}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    outs, errs = p.communicate(timeout=15)
    return outs.decode("utf-8").strip()


def write_data_as_json_to_file(file, data):
    with open(file, 'w') as test_results_file:
        json.dump(data, test_results_file, indent=2)


def print_file(file, number_of_lines = 0):
    with open(file) as f:
        contents = f.read()
    if number_of_lines:
        for index, line in enumerate(contents.split(os.linesep)):
            if index < number_of_lines + 1:
                print(line, flush=True)
            else: break
    else: print(contents, flush=True)


def get_process_info(proc_name):
    for proc in process_iter():
        if proc_name in proc.name():
            return proc


def stop_process(proc_name):
    for proc in process_iter():
        if proc_name in proc.name():
            print(f" --- Terminating the {proc_name} process - {proc}", flush=True)
            proc.terminate()
    time.sleep(30)
    for proc in process_iter():
        if proc_name in proc.name():
            print(f" !!! ERROR: {proc_name} process is still active. Killing forcefully - {proc}", flush=True)
            proc.kill()


def show_percentage(part, whole):
    return round(100 * float(part) / float(whole), 2)


def get_current_date_time():
    now = datetime.now()
    return now.strftime("%d/%m/%Y %H:%M:%S")


def get_file_creation_date(path_to_file):
    return time.ctime(os.path.getmtime(path_to_file))


def create_dir(dir_name, root='.'):
    Path(f"{root}/{dir_name}").mkdir(parents=True, exist_ok=True)
    return f"{root}/{dir_name}"


def remove_dir(dir_name):
    try:
        shutil.rmtree(dir_name)
    except OSError as e:
        print("Error: %s : %s" % (dir_name, e.strerror))


def get_directory_size(start_path='.'):
    total_size_in_bytes = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size_in_bytes += os.path.getsize(fp)
    return total_size_in_bytes


def zip_file(archive_name, file_path):
    with zipfile.ZipFile(archive_name, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip:
        file_name = basename(normpath(file_path))
        zip.write(file_path, arcname=file_name)


def unzip_file(file_name):
    with zipfile.ZipFile(file_name, 'r') as zip:
        zip.printdir()

        print(f"Extracting all the files from {file_name}...")
        zip.extractall()


def delete_file(file_path):
    # file_path => a Path (pathlib object)
    try:
        file_path.unlink()
    except OSError as e:
        print(f"Error: {file_path} : {e.strerror}")


def get_file_sha_256_sum(filename):
    with open(filename,"rb") as f:
        bytes = f.read()
        readable_hash = hashlib.sha256(bytes).hexdigest();
        return readable_hash

        
def print_n_last_lines_from_file(n, file_name):
    logs = subprocess.run(['tail', "-n", f"{n}", f"{file_name}"], stdout=subprocess.PIPE).stdout.decode('utf-8').strip().rstrip().splitlines()
    print("")
    for line in logs:
        print(line, flush=True)
    print("")

    
def execute_command(command):
    print(f"--- Execute command {command}")
    try:
        cmd = shlex.split(command)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        outs, errors = process.communicate(timeout=3600)               
        if errors:
            print(f"Warnings or Errors: {errors}", flush=True)
        print(f"Output of command: {command} : {outs}", flush=True)                    
        exit_code = process.returncode
        if (exit_code != 0):
            print(f"Command {command} returned exit code: {exit_code}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Command {command} returned exception: {e}")
        raise
    
  
def get_last_perf_stats_point():
    try:
        last_perf_stats_point = db_sync_perf_stats[-1]
    except Exception as e:
        print(f"Exception in get_last_perf_stats_point: {e}")
        stats_data_point = {"time": 0, "slot_no": 0, "cpu_percent_usage": 0, "rss_mem_usage": 0}
        db_sync_perf_stats.append(stats_data_point)
        last_perf_stats_point = db_sync_perf_stats[-1]

    return last_perf_stats_point
    

def should_skip(args):
    return vars(args)["run_only_sync_test"]

    
def get_environment(args):
    return vars(args)["environment"]


def get_node_pr(args):
    return str(vars(args)["node_pr"]).strip()


def get_node_branch(args):
    return str(vars(args)["node_branch"]).strip()


def get_node_version_from_gh_action(args):
    return str(vars(args)["node_version_gh_action"]).strip()


def get_db_pr(args):
    return str(vars(args)["db_sync_pr"]).strip()


def get_db_sync_branch(args):
    return str(vars(args)["db_sync_branch"]).strip()


def get_db_sync_start_options(args):
    options = str(vars(args)["db_sync_start_options"]).strip()
    if options == "--none":
        return ''
    return options


def get_snapshot_url(args):
    return str(vars(args)["snapshot_url"]).strip()


def get_db_sync_version_from_gh_action(args):
    return str(vars(args)["db_sync_version_gh_action"]).strip()    


def get_testnet_value(env):
    if env == "mainnet":
        return "--mainnet"
    if env == "preprod":
        return "--testnet-magic 1"
    if env == "preview":
        return "--testnet-magic 2"
    elif env == "shelley-qa":
        return "--testnet-magic 3"
    elif env == "staging":
        return "--testnet-magic 633343913"
    else:
        return None


def get_log_output_frequency(env):
    if env == "mainnet":
        return 20
    else:
        return 3


def export_epoch_sync_times_from_db(env, file, snapshot_epoch_no = 0):
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    try:
        p = subprocess.Popen(["psql", f"{env}", "-t", "-c", f"\o {file}", "-c", f"SELECT array_to_json(array_agg(epoch_sync_time), FALSE) FROM epoch_sync_time where no >= {snapshot_epoch_no};" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = (p.decode("utf-8").strip() for p in p.communicate(timeout=600))
        if err:
            print(f"Error during exporting epoch sync times from db: {err}. Killing extraction process.", flush=True)
            p.kill()
        return out
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        print(f"Error during exporting epoch sync times from db: {e}. Killing extraction process.", flush=True)
    except Exception as e:
        print(f"Error during exporting epoch sync times from db: {e}. Killing extraction process.", flush=True)
        p.kill()


def emergency_upload_artifacts(env):
    write_data_as_json_to_file(DB_SYNC_PERF_STATS, db_sync_perf_stats)
    export_epoch_sync_times_from_db(env, EPOCH_SYNC_TIMES)

    zip_file(PERF_STATS_ARCHIVE, DB_SYNC_PERF_STATS)
    zip_file(SYNC_DATA_ARCHIVE, EPOCH_SYNC_TIMES)
    zip_file(DB_SYNC_ARCHIVE, DB_SYNC_LOG)
    zip_file(NODE_ARCHIVE, NODE_LOG)

    upload_artifact(PERF_STATS_ARCHIVE)
    upload_artifact(SYNC_DATA_ARCHIVE)
    upload_artifact(DB_SYNC_ARCHIVE)
    upload_artifact(NODE_ARCHIVE)

    stop_process('cardano-db-sync')
    stop_process('cardano-node')


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


def get_node_archive_url(node_pr):
    cardano_node_pr=f"-pr-{node_pr}"
    return f"https://hydra.iohk.io/job/Cardano/cardano-node{cardano_node_pr}/linux.musl.cardano-node-linux/latest-finished/download/1/"


def get_node_config_files(env):
    base_url = "https://book.world.dev.cardano.org/environments/"
    urllib.request.urlretrieve(base_url + env + "/config.json", env + "-config.json",)
    urllib.request.urlretrieve(base_url + env + "/byron-genesis.json", "byron-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "/shelley-genesis.json", "shelley-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "/alonzo-genesis.json", "alonzo-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "/conway-genesis.json", "conway-genesis.json",)
    urllib.request.urlretrieve(base_url + env + "/topology.json", env + "-topology.json",)


def copy_node_executables(build_method="nix"):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    node_dir = Path.cwd() / 'cardano-node'
    os.chdir(node_dir)
    
    if build_method == "nix":
        node_binary_location = "cardano-node-bin/bin/cardano-node"
        node_cli_binary_location = "cardano-cli-bin/bin/cardano-cli"
        shutil.copy2(node_binary_location, "_cardano-node")
        shutil.copy2(node_cli_binary_location, "_cardano-cli")
        os.chdir(current_directory)
        return
    
    # Path for copying binaries built with cabal
    try:
        find_node_cmd = [ "find", ".", "-name", "cardano-node", "-executable", "-type", "f" ]
        output_find_node_cmd = (
            subprocess.check_output(find_node_cmd, stderr=subprocess.STDOUT, timeout=15)
            .decode("utf-8")
            .strip()
        )
        print(f"Find cardano-node output: {output_find_node_cmd}")
        shutil.copy2(output_find_node_cmd, "_cardano-node")

        find_cli_cmd = [ "find", ".", "-name", "cardano-cli", "-executable", "-type", "f" ]
        output_find_cli_cmd = (
            subprocess.check_output(find_cli_cmd, stderr=subprocess.STDOUT, timeout=15)
            .decode("utf-8")
            .strip()
        )
        print(f"Find cardano-cli output: {output_find_cli_cmd}")
        shutil.copy2(output_find_cli_cmd, "_cardano-cli")       
        os.chdir(current_directory)

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def get_node_version():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    try:
        cmd = "./_cardano-cli --version"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        cardano_cli_version = output.split("git rev ")[0].strip()
        cardano_cli_git_rev = output.split("git rev ")[1].strip()
        os.chdir(current_directory)
        return str(cardano_cli_version), str(cardano_cli_git_rev)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def download_and_extract_node_snapshot(env):
    current_directory = os.getcwd()
    headers = {'User-Agent': 'Mozilla/5.0'}
    if env == "mainnet":
        snapshot_url = 'https://update-cardano-mainnet.iohk.io/cardano-node-state/db-mainnet.tar.gz'
    else:
        snapshot_url = '' # no other environments are supported for now

    archive_name = f"db-{env}.tar.gz"
    
    print("Download node snapshot file:")
    print(f" - current_directory: {current_directory}")
    print(f" - download_url: {snapshot_url}")
    print(f" - archive name: {archive_name}")

    with requests.get(snapshot_url, headers = headers, stream = True, timeout = 2800) as r:
        r.raise_for_status()
        with open(archive_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")
    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    os.rename(f"db-{env}","db")
    delete_file(Path(current_directory) / archive_name)
    print(f" ------ listdir (after archive extraction): {os.listdir(current_directory)}")


def set_node_socket_path_env_var_in_cwd():    
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    current_directory = os.getcwd()
    if not 'cardano-node' == basename(normpath(current_directory)):
        raise Exception(f"You're not inside 'cardano-node' directory but in: {current_directory}")
    socket_path = 'db/node.socket'
    export_env_var("CARDANO_NODE_SOCKET_PATH", socket_path)


def get_node_tip(env, timeout_minutes=20):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    cmd = "./_cardano-cli query tip " + get_testnet_value(env)

    for i in range(timeout_minutes):
        try:
            output = (
                subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8").strip()
            )
            os.chdir(current_directory)
            output_json = json.loads(output)            
            if output_json["epoch"] is not None:
                output_json["epoch"] = int(output_json["epoch"])
            if "block" not in output_json:
                output_json["block"] = None
            else:
                output_json["block"] = int(output_json["block"])
            if "hash" not in output_json:
                output_json["hash"] = None
            if "slot" not in output_json:
                output_json["slot"] = None
            else:
                output_json["slot"] = int(output_json["slot"])
            if "syncProgress" not in output_json:
                output_json["syncProgress"] = None
            else:
                output_json["syncProgress"] = float(output_json["syncProgress"])

            return output_json["epoch"], output_json["block"], output_json["hash"], \
                   output_json["slot"], output_json["era"].lower(), output_json["syncProgress"]
        except subprocess.CalledProcessError as e:
            print(f" === Waiting 60s before retrying to get the tip again - {i}")
            print(f"     !!!ERROR: command {e.cmd} return with error (code {e.returncode}): {' '.join(str(e.output).split())}")
            if "Invalid argument" in str(e.output):
                emergency_upload_artifacts(env)
                exit(1)
            pass
        time.sleep(ONE_MINUTE)
    emergency_upload_artifacts(env)
    exit(1)


def wait_for_node_to_start(env):
    # when starting from clean state it might take ~30 secs for the cli to work
    # when starting from existing state it might take >10 mins for the cli to work (opening db and
    # replaying the ledger)
    start_counter = time.perf_counter()
    get_node_tip(env)
    stop_counter = time.perf_counter()

    start_time_seconds = int(stop_counter - start_counter)
    print(f" === It took {start_time_seconds} seconds for the QUERY TIP command to be available")
    return start_time_seconds


def wait_for_node_to_sync(env, sync_percentage = 99.9):
    start_sync = time.perf_counter()
    *data, node_sync_progress = get_node_tip(env)
    log_frequency = get_log_output_frequency(env)
    print("--- Waiting for Node to sync")
    print(f"node progress [%]: {node_sync_progress}")
    counter = 0

    while node_sync_progress < sync_percentage:
        if counter % log_frequency == 0:
            node_epoch_no, node_block_no, node_hash, node_slot, node_era, node_sync_progress = get_node_tip(env)
            print(f"node progress [%]: {node_sync_progress}, epoch: {node_epoch_no}, block: {node_block_no}, slot: {node_slot}, era: {node_era}")
        *data, node_sync_progress = get_node_tip(env)
        time.sleep(ONE_MINUTE)
        counter += 1

    end_sync = time.perf_counter()
    sync_time_seconds = int(end_sync - start_sync)
    return sync_time_seconds

    
def start_node_in_cwd(env):
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    current_directory = os.getcwd()
    if not 'cardano-node' == basename(normpath(current_directory)):
        raise Exception(f"You're not inside 'cardano-node' directory but in: {current_directory}")
       
    print(f"current_directory: {current_directory}")
    cmd = (
        f"./_cardano-node run --topology {env}-topology.json --database-path "
        f"{Path(ROOT_TEST_PATH) / 'cardano-node' / 'db'} "
        f"--host-addr 0.0.0.0 --port 3000 --config "
        f"{env}-config.json --socket-path ./db/node.socket"
    )

    logfile = open(NODE_LOG, "w+")
    print(f"start node cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd.split(" "), stdout=logfile, stderr=logfile)
        print("waiting for db folder to be created")
        counter = 0
        timeout_counter = 25 * ONE_MINUTE
        node_db_dir = current_directory + "/db"
        while not os.path.isdir(node_db_dir):
            time.sleep(1)
            counter += 1
            if counter > timeout_counter:
                print(
                    f"ERROR: waited {timeout_counter} seconds and the DB folder was not created yet")
                exit(1)

        print(f"DB folder was created after {counter} seconds")
        secs_to_start = wait_for_node_to_start(env)
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(node_db_dir)}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def create_pgpass_file(env):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    db_sync_config_dir = Path.cwd() / 'cardano-db-sync' / 'config'
    os.chdir(db_sync_config_dir)

    pgpass_file = f"pgpass-{env}"
    POSTGRES_PORT = os.getenv('PGPORT')
    pgpass_content = f"{POSTGRES_DIR}:{POSTGRES_PORT}:{env}:{POSTGRES_USER}:*"
    export_env_var("PGPASSFILE", f"config/pgpass-{env}")

    with open(pgpass_file, "w") as pgpass_text_file:
        print(pgpass_content, file=pgpass_text_file)
    os.chmod(pgpass_file, 0o600)
    os.chdir(current_directory)


def create_database(): 
    os.chdir(ROOT_TEST_PATH)
    db_sync_dir = Path.cwd() / 'cardano-db-sync'
    os.chdir(db_sync_dir)

    try:
        cmd = ["scripts/postgresql-setup.sh", "--createdb"]
        output = (
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            .decode("utf-8")
            .strip()
        )
        print(f"Create database script output: {output}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )
    if "All good!" not in output:
        raise RuntimeError("Create database has not ended successfully")


def copy_db_sync_executables(build_method="nix"):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    db_sync_dir = Path.cwd() / 'cardano-db-sync'
    os.chdir(db_sync_dir)
    
    if build_method == "nix":
        db_sync_binary_location = "db-sync-node/bin/cardano-db-sync"
        db_tool_binary_location = "db-sync-tool/bin/cardano-db-tool"
        shutil.copy2(db_sync_binary_location, "_cardano-db-sync")
        shutil.copy2(db_tool_binary_location, "_cardano-db-tool")
        os.chdir(current_directory)
        return

    try:
        find_db_cmd = [ "find", ".", "-name", "cardano-db-sync", "-executable", "-type", "f" ]
        output_find_db_cmd = (
            subprocess.check_output(find_db_cmd, stderr=subprocess.STDOUT, timeout=15)
            .decode("utf-8")
            .strip()
        )
        os.chdir(current_directory)
        print(f"Find cardano-db-sync output: {output_find_db_cmd}")
        shutil.copy2(output_find_db_cmd, "_cardano-db-sync")

        find_db_tool_cmd = [ "find", ".", "-name", "cardano-db-tool", "-executable", "-type", "f" ]
        output_find_db_tool_cmd = (
            subprocess.check_output(find_db_tool_cmd, stderr=subprocess.STDOUT, timeout=15)
            .decode("utf-8")
            .strip()
        )

        print(f"Find cardano-db-tool output: {output_find_db_tool_cmd}")
        shutil.copy2(output_find_db_tool_cmd, "_cardano-db-tool")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def get_db_sync_archive_url(db_pr):
    cardano_db_sync_pr=f"-pr-{db_pr}"
    return f"https://hydra.iohk.io/job/Cardano/cardano-db-sync{cardano_db_sync_pr}/cardano-db-sync-linux/latest-finished/download/1/"


def get_db_sync_version():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    try:
        cmd = "./_cardano-db-sync --version"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        cardano_db_sync_version = output.split("git revision ")[0].strip()
        cardano_db_sync_git_revision = output.split("git revision ")[1].strip()
        os.chdir(current_directory)
        return str(cardano_db_sync_version), str(cardano_db_sync_git_revision)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def get_latest_snapshot_url(env, args):
    github_snapshot_url = get_snapshot_url(args)
    if github_snapshot_url != "latest":
        return github_snapshot_url

    if env == "mainnet":
        general_snapshot_url = "https://update-cardano-mainnet.iohk.io/?list-type=2&delimiter=/&prefix=cardano-db-sync/&max-keys=50&cachestamp=459588"
    else:
        raise ValueError('Snapshot are currently available only for mainnet environment')

    headers = {'Content-type': 'application/json'}
    res_with_latest_db_sync_version = requests.get(general_snapshot_url, headers=headers)
    dict_with_latest_db_sync_version = xmltodict.parse(res_with_latest_db_sync_version.content)
    db_sync_latest_version_prefix = dict_with_latest_db_sync_version["ListBucketResult"]["CommonPrefixes"]["Prefix"]

    if env == "mainnet":
        latest_snapshots_list_url = f"https://update-cardano-mainnet.iohk.io/?list-type=2&delimiter=/&prefix={db_sync_latest_version_prefix}&max-keys=50&cachestamp=462903"
    else:
        raise ValueError('Snapshot are currently available only for mainnet environment')

    res_snapshots_list = requests.get(latest_snapshots_list_url, headers=headers)
    dict_snapshots_list = xmltodict.parse(res_snapshots_list.content)
    latest_snapshot = dict_snapshots_list["ListBucketResult"]["Contents"][-2]["Key"]
   
    if env == "mainnet":
        latest_snapshot_url = f"https://update-cardano-mainnet.iohk.io/{latest_snapshot}"
    else:
        raise ValueError('Snapshot are currently available only for mainnet environment')

    return latest_snapshot_url


def download_db_sync_snapshot(snapshot_url):
    current_directory = os.getcwd()
    headers = {'User-Agent': 'Mozilla/5.0'}
    archive_name = snapshot_url.split("/")[-1].strip()

    print("Download db-sync snapshot file:")
    print(f" - current_directory: {current_directory}")
    print(f" - download_url: {snapshot_url}")
    print(f" - archive name: {archive_name}")

    with requests.get(snapshot_url, headers = headers, stream = True, timeout = 60 * 60) as r:
        r.raise_for_status()
        with open(archive_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return archive_name


def get_snapshot_sha_256_sum(snapshot_url):
    snapshot_sha_256_sum_url = snapshot_url + ".sha256sum"
    for line in requests.get(snapshot_sha_256_sum_url):
        return line.decode('utf-8').split(" ")[0]


def restore_db_sync_from_snapshot(env, snapshot_file, remove_ledger_dir="yes"):
    os.chdir(ROOT_TEST_PATH)
    if remove_ledger_dir == "yes":
        ledger_state_dir = Path.cwd() / 'cardano-db-sync' / 'ledger-state' / f"{env}"
        remove_dir(ledger_state_dir)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    
    ledger_dir = create_dir(f"ledger-state/{env}")
    print(f"ledger_dir: {ledger_dir}")
    
    # set tmp to local dir in current partition due to buildkite agent space 
    # limitation on /tmp which is not big enough for snapshot restoration
    TMP_DIR=create_dir('tmp')
    export_env_var("TMPDIR", TMP_DIR)

    export_env_var("PGPASSFILE", f"config/pgpass-{env}")
    export_env_var("ENVIRONMENT", f"{env}")
    export_env_var("RESTORE_RECREATE_DB", "N")
    start_restoration = time.perf_counter()

    p = subprocess.Popen(["scripts/postgresql-setup.sh", "--restore-snapshot", f"{snapshot_file}", f"{ledger_dir}"], stdout=subprocess.PIPE)
    try:     
        outs, errs = p.communicate(timeout=36000)
        output = outs.decode("utf-8")
        print(f"Restore database: {output}")
        if errs:
            errors = errs.decode("utf-8")
            print(f"Error during restoration: {errors}")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )
    except subprocess.TimeoutExpired as e:
        p.kill()
        print(e)

    finally:
        export_env_var("TMPDIR", "/tmp")

    if "All good!" not in outs.decode("utf-8"):
        raise RuntimeError("Restoration has not ended successfully")

    end_restoration = time.perf_counter()
    return int(end_restoration - start_restoration)


def create_db_sync_snapshot_stage_1(env):
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    export_env_var("PGPASSFILE", f"config/pgpass-{env}")

    cmd = f"./_cardano-db-tool prepare-snapshot --state-dir ledger-state/{env}"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')

    try:
        outs, errs = p.communicate(timeout=300)
        if errs:
            print(f"Warnings or Errors: {errs}")
        final_line_with_script_cmd = outs.split("\n")[2].lstrip()
        print(f"Snapshot Creation - Stage 1 result: {final_line_with_script_cmd}")
        return final_line_with_script_cmd

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )

def create_db_sync_snapshot_stage_2(stage_2_cmd, env):
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-db-sync')
    export_env_var("PGPASSFILE", f"config/pgpass-{env}")

    cmd = f"{stage_2_cmd}"
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')

    try:
        outs, errs = p.communicate(timeout=43200) # 12 hours
        print(f"Snapshot Creation - Stage 2 result: {outs}")
        if errs:
            print(f"Warnings or Errors: {errs}")
        return outs.split("\n")[3].lstrip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )

        
def get_db_sync_tip(env):
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{env}",  "-c", "select epoch_no, block_no, slot_no from block order by id desc limit 1;" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    should_try = True
    counter = 0

    while should_try:
        try:
            outs, errs = p.communicate(timeout=180)
            output_string = outs.decode("utf-8")
            epoch_no, block_no, slot_no = [e.strip() for e in outs.decode("utf-8").split("|")]
            return epoch_no, block_no, slot_no
        except Exception as e:
            if counter > 5:
                should_try = False
                emergency_upload_artifacts(env)
                print(e)
                p.kill()
                raise
            print(f"db-sync tip data unavailable, possible postgress failure. Output from psql: {output_string}")
            counter += 1
            print(e)
            print(errs)
            time.sleep(ONE_MINUTE)


def get_db_sync_progress(env):
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{env}",  "-c", "select 100 * (extract (epoch from (max (time) at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) / (extract (epoch from (now () at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) as sync_percent from block ;" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    should_try = True
    counter = 0

    while should_try:
        try:
            outs, errs = p.communicate(timeout=300)
            progress_string = outs.decode("utf-8")
            db_sync_progress = round(float(progress_string), 2)
            return db_sync_progress
        except Exception as e:
            if counter > 5:
                should_try = False
                emergency_upload_artifacts(env)
                p.kill()
                raise
            print(f"db-sync progress unavailable, possible postgress failure. Output from psql: {progress_string}")
            counter += 1
            time.sleep(ONE_MINUTE)


def wait_for_db_to_sync(env, sync_percentage = 99.9):
    db_sync_perf_stats.clear()
    start_sync = time.perf_counter()
    last_rollback_time = time.perf_counter()
    db_sync_progress = get_db_sync_progress(env)
    buildkite_timeout_in_sec = 1828000
    counter = 0
    rollback_counter = 0
    db_sync_process = get_process_info('cardano-db-sync')
    log_frequency = get_log_output_frequency(env)

    print("--- Db sync monitoring", flush=True)
    while db_sync_progress < sync_percentage:
        sync_time_in_sec = time.perf_counter() - start_sync
        if sync_time_in_sec + 5 * ONE_MINUTE > buildkite_timeout_in_sec:
            emergency_upload_artifacts(env)
            raise Exception('Emergency uploading artifacts before buid timeout exception...')
        if counter % 5 == 0:
            current_progress = get_db_sync_progress(env)
            if current_progress < db_sync_progress and db_sync_progress > 3:
                print(f"Progress decreasing - current progress: {current_progress} VS previous: {db_sync_progress}.")
                print("Possible rollback... Printing last 10 lines of log")
                print_n_last_lines_from_file(10, DB_SYNC_LOG)
                if time.perf_counter() - last_rollback_time > 10 * ONE_MINUTE:
                    print("Resetting previous rollback counter as there was no progress decrease for more than 10 minutes", flush=True)
                    rollback_counter = 0
                last_rollback_time = time.perf_counter()
                rollback_counter += 1
                print(f"Rollback counter: {rollback_counter} out of 15")
            if rollback_counter > 15:
                print(f"Progress decreasing for {rollback_counter * counter} minutes.", flush=True)
                print(f"Shutting down all services and emergency uploading artifacts", flush=True)
                emergency_upload_artifacts(env)
                raise Exception('Rollback taking too long. Shutting down...')
        if counter % log_frequency == 0:
            node_epoch_no, node_block_no, node_hash, node_slot, node_era, node_sync_progress = get_node_tip(env)
            print(f"node progress [%]: {node_sync_progress}, epoch: {node_epoch_no}, block: {node_block_no}, slot: {node_slot}, era: {node_era}", flush=True)
            epoch_no, block_no, slot_no = get_db_sync_tip(env)
            db_sync_progress = get_db_sync_progress(env)
            sync_time_h_m_s = seconds_to_time(time.perf_counter() - start_sync)
            print(f"db sync progress [%]: {db_sync_progress}, sync time [h:m:s]: {sync_time_h_m_s}, epoch: {epoch_no}, block: {block_no}, slot: {slot_no}", flush=True)
            print_n_last_lines_from_file(5, DB_SYNC_LOG)

        try:
            time_point = int(time.perf_counter() - start_sync)
            _, _, slot_no = get_db_sync_tip(env)
            cpu_usage = db_sync_process.cpu_percent(interval=None)
            rss_mem_usage = db_sync_process.memory_info()[0]
        except Exception as e:
            end_sync = time.perf_counter()
            db_full_sync_time_in_secs = int(end_sync - start_sync)
            print("Unexpected error during sync process", flush=True)
            print(e)
            emergency_upload_artifacts(env)
            return db_full_sync_time_in_secs

        stats_data_point = {"time": time_point, "slot_no": slot_no, "cpu_percent_usage": cpu_usage, "rss_mem_usage": rss_mem_usage}
        db_sync_perf_stats.append(stats_data_point)
        write_data_as_json_to_file(DB_SYNC_PERF_STATS, db_sync_perf_stats)
        time.sleep(ONE_MINUTE)
        counter += 1

    end_sync = time.perf_counter()
    sync_time_seconds = int(end_sync - start_sync)
    print(f"db sync progress [%] before finalizing process: {db_sync_progress}", flush=True)
    return sync_time_seconds


def get_total_db_size(env):
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    cmd = ["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{env}", "-c", f"SELECT pg_size_pretty( pg_database_size('{env}') );" ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        outs, errs = p.communicate(timeout=60)
        if errs:
            print(f"Error in get database size: {errs}")
        return outs.rstrip().strip()
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise
    except Exception as e:
        p.kill()
        raise


def start_db_sync(env, start_args="", first_start="True"):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    export_env_var("DB_SYNC_START_ARGS", start_args)
    export_env_var("FIRST_START", f"{first_start}")
    export_env_var("ENVIRONMENT", env)
    export_env_var("LOG_FILEPATH", DB_SYNC_LOG)

    try:
        cmd = "./scripts/db-sync-start.sh"
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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


def get_file_size(file):
    file_stats = os.stat(file)
    file_size_in_mb = int(file_stats.st_size / (1000 * 1000))
    return file_size_in_mb


def is_string_present_in_file(file_to_check, search_string):
    encoded_search_string = str.encode(search_string)
    with open(file_to_check, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        if s.find(encoded_search_string) != -1:
            s.seek(s.find(encoded_search_string))
            print(s.readline().decode("utf-8"))
            return "Yes"
        return "No"


def are_errors_present_in_db_sync_logs(log_file):
    return is_string_present_in_file(log_file, "db-sync-node:Error")


def are_rollbacks_present_in_db_sync_logs(log_file):
    with open(log_file, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        initial_rollback_position = s.find(b'rolling')
        offset = s.find(b'rolling', initial_rollback_position + len('rolling'))
        if offset != -1:
            s.seek(offset)
            if s.find(b'rolling'):
                return "Yes"
        return "No"


def setup_postgres(pg_dir=POSTGRES_DIR, pg_user=POSTGRES_USER, pg_port='5432'):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    
    export_env_var("POSTGRES_DIR", pg_dir)
    export_env_var("PGHOST", 'localhost')
    export_env_var("PGUSER", pg_user)
    export_env_var("PGPORT", pg_port)

    try:
        cmd = ["./scripts/postgres-start.sh", f"{pg_dir}", "-k"]
        output = (
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
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


def list_databases():
    cmd = ["psql", "-U", f"{POSTGRES_USER}", "-l" ]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')

    try:
        outs, errs = p.communicate(timeout=60)
        print(f"List databases: {outs}")
        if errs:
            print(f"Error in list databases: {errs}")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise


def get_db_schema():
    try:
        conn = psycopg2.connect(
            database=f'{ENVIRONMENT}', user=f'{POSTGRES_USER}'
        )
        cursor = conn.cursor()
        get_all_tables = 'SELECT table_name FROM information_schema.tables WHERE table_schema=\'public\''
        cursor.execute(get_all_tables)
        tabels = cursor.fetchall();

        db_schema = {}
        for table in tabels:
            table_name = table[0]
            get_table_fields_and_attributes = f'SELECT a.attname as "Column", pg_catalog.format_type(a.atttypid, a.atttypmod) as "Datatype" FROM pg_catalog.pg_attribute a WHERE a.attnum > 0 AND NOT a.attisdropped AND a.attrelid = ( SELECT c.oid FROM pg_catalog.pg_class c LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE c.relname ~ \'^{table_name}$\' AND pg_catalog.pg_table_is_visible(c.oid));'
            cursor.execute(get_table_fields_and_attributes)
            table_with_attributes = cursor.fetchall()
            attributes = []
            table_schema = {}
            for row in table_with_attributes:
                attributes.append(row)
                table_schema.update({str(table_name) : attributes }) 
            db_schema.update({str(table_name) : attributes })
        cursor.close()
        conn.commit()
        conn.close()
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()

    return db_schema


def get_db_indexes():
    try:
        conn = psycopg2.connect(
            database=f'{ENVIRONMENT}', user=f'{POSTGRES_USER}'
        )
        cursor = conn.cursor()

        get_all_tables = f'select tbl.relname as table_name from pg_index pgi join pg_class idx on idx.oid = pgi.indexrelid join pg_namespace insp on insp.oid = idx.relnamespace join pg_class tbl on tbl.oid = pgi.indrelid join pg_namespace tnsp on tnsp.oid = tbl.relnamespace where pgi.indisunique and tnsp.nspname = \'public\';'
        cursor.execute(get_all_tables)          
        tables = cursor.fetchall()
        all_indexes = {}

        for table in tables:
            table_name =table[0]
            get_table_and_index = f'select tbl.relname as table_name, idx.relname as index_name from pg_index pgi join pg_class idx on idx.oid = pgi.indexrelid join pg_namespace insp on insp.oid = idx.relnamespace join pg_class tbl on tbl.oid = pgi.indrelid join pg_namespace tnsp on tnsp.oid = tbl.relnamespace where pgi.indisunique and tnsp.nspname = \'public\' and tbl.relname = \'{table_name}\';'
            cursor.execute(get_table_and_index)          
            table_and_index = cursor.fetchall()
            indexes = []
            table_indexes = {}
            for table, index in table_and_index:
                indexes.append(index)
                table_indexes.update({str(table_name) : indexes }) 
            all_indexes.update({str(table_name) : indexes })         
        cursor.close()
        conn.commit()
        conn.close()
        return all_indexes
    except (Exception, psycopg2.DatabaseError) as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()
            
    return all_indexes


def check_database(fn, err_msg, expected_value):
    try:
        assert_that(fn()).described_as(err_msg).is_equal_to(expected_value)
    except AssertionError as e:
        print_color_log(sh_colors.WARNING, f'Warning - validation errors: {e}\n\n')
        return e


EXPECTED_DB_SCHEMA = {
    'schema_version': [('id', 'bigint'), ('stage_one', 'bigint'),
                       ('stage_two', 'bigint'), ('stage_three', 'bigint'
                       )],
    'pool_update': [
        ('id', 'bigint'),
        ('hash_id', 'bigint'),
        ('cert_index', 'integer'),
        ('vrf_key_hash', 'hash32type'),
        ('pledge', 'lovelace'),
        ('active_epoch_no', 'bigint'),
        ('meta_id', 'bigint'),
        ('margin', 'double precision'),
        ('fixed_cost', 'lovelace'),
        ('registered_tx_id', 'bigint'),
        ('reward_addr_id', 'bigint'),
        ],
    'pool_owner': [('id', 'bigint'), ('addr_id', 'bigint'),
                   ('pool_update_id', 'bigint')],
    'pool_metadata_ref': [('id', 'bigint'), ('pool_id', 'bigint'),
                          ('url', 'character varying'), ('hash',
                          'hash32type'), ('registered_tx_id', 'bigint'
                          )],
    'ada_pots': [
        ('id', 'bigint'),
        ('slot_no', 'word63type'),
        ('epoch_no', 'word31type'),
        ('treasury', 'lovelace'),
        ('reserves', 'lovelace'),
        ('rewards', 'lovelace'),
        ('utxo', 'lovelace'),
        ('deposits', 'lovelace'),
        ('fees', 'lovelace'),
        ('block_id', 'bigint'),
        ],
    'pool_retire': [('id', 'bigint'), ('hash_id', 'bigint'),
                    ('cert_index', 'integer'), ('announced_tx_id',
                    'bigint'), ('retiring_epoch', 'word31type')],
    'pool_hash': [('id', 'bigint'), ('hash_raw', 'hash28type'), ('view'
                  , 'character varying')],
    'slot_leader': [('id', 'bigint'), ('hash', 'hash28type'),
                    ('pool_hash_id', 'bigint'), ('description',
                    'character varying')],
    'block': [
        ('id', 'bigint'),
        ('hash', 'hash32type'),
        ('epoch_no', 'word31type'),
        ('slot_no', 'word63type'),
        ('epoch_slot_no', 'word31type'),
        ('block_no', 'word31type'),
        ('previous_id', 'bigint'),
        ('slot_leader_id', 'bigint'),
        ('size', 'word31type'),
        ('time', 'timestamp without time zone'),
        ('tx_count', 'bigint'),
        ('proto_major', 'word31type'),
        ('proto_minor', 'word31type'),
        ('vrf_key', 'character varying'),
        ('op_cert', 'hash32type'),
        ('op_cert_counter', 'word63type'),
        ],
    'tx': [
        ('id', 'bigint'),
        ('hash', 'hash32type'),
        ('block_id', 'bigint'),
        ('block_index', 'word31type'),
        ('out_sum', 'lovelace'),
        ('fee', 'lovelace'),
        ('deposit', 'bigint'),
        ('size', 'word31type'),
        ('invalid_before', 'word64type'),
        ('invalid_hereafter', 'word64type'),
        ('valid_contract', 'boolean'),
        ('script_size', 'word31type'),
        ],
    'stake_address': [('id', 'bigint'), ('hash_raw', 'addr29type'),
                      ('view', 'character varying'), ('script_hash',
                      'hash28type')],
    'redeemer': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('unit_mem', 'word63type'),
        ('unit_steps', 'word63type'),
        ('fee', 'lovelace'),
        ('purpose', 'scriptpurposetype'),
        ('index', 'word31type'),
        ('script_hash', 'hash28type'),
        ('redeemer_data_id', 'bigint'),
        ],
    'tx_out': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('index', 'txindex'),
        ('address', 'character varying'),
        ('address_raw', 'bytea'),
        ('address_has_script', 'boolean'),
        ('payment_cred', 'hash28type'),
        ('stake_address_id', 'bigint'),
        ('value', 'lovelace'),
        ('data_hash', 'hash32type'),
        ('inline_datum_id', 'bigint'),
        ('reference_script_id', 'bigint'),
        ],
    'datum': [('id', 'bigint'), ('hash', 'hash32type'), ('tx_id',
              'bigint'), ('value', 'jsonb'), ('bytes', 'bytea')],
    'tx_in': [('id', 'bigint'), ('tx_in_id', 'bigint'), ('tx_out_id',
              'bigint'), ('tx_out_index', 'txindex'), ('redeemer_id',
              'bigint')],
    'collateral_tx_in': [('id', 'bigint'), ('tx_in_id', 'bigint'),
                         ('tx_out_id', 'bigint'), ('tx_out_index',
                         'txindex')],
    'epoch': [
        ('id', 'bigint'),
        ('out_sum', 'word128type'),
        ('fees', 'lovelace'),
        ('tx_count', 'word31type'),
        ('blk_count', 'word31type'),
        ('no', 'word31type'),
        ('start_time', 'timestamp without time zone'),
        ('end_time', 'timestamp without time zone'),
        ],
    'pool_relay': [
        ('id', 'bigint'),
        ('update_id', 'bigint'),
        ('ipv4', 'character varying'),
        ('ipv6', 'character varying'),
        ('dns_name', 'character varying'),
        ('dns_srv_name', 'character varying'),
        ('port', 'integer'),
        ],
    'stake_registration': [('id', 'bigint'), ('addr_id', 'bigint'),
                           ('cert_index', 'integer'), ('epoch_no',
                           'word31type'), ('tx_id', 'bigint')],
    'stake_deregistration': [
        ('id', 'bigint'),
        ('addr_id', 'bigint'),
        ('cert_index', 'integer'),
        ('epoch_no', 'word31type'),
        ('tx_id', 'bigint'),
        ('redeemer_id', 'bigint'),
        ],
    'delegation': [
        ('id', 'bigint'),
        ('addr_id', 'bigint'),
        ('cert_index', 'integer'),
        ('pool_hash_id', 'bigint'),
        ('active_epoch_no', 'bigint'),
        ('tx_id', 'bigint'),
        ('slot_no', 'word63type'),
        ('redeemer_id', 'bigint'),
        ],
    'tx_metadata': [('id', 'bigint'), ('key', 'word64type'), ('json',
                    'jsonb'), ('bytes', 'bytea'), ('tx_id', 'bigint')],
    'reward': [
        ('id', 'bigint'),
        ('addr_id', 'bigint'),
        ('type', 'rewardtype'),
        ('amount', 'lovelace'),
        ('earned_epoch', 'bigint'),
        ('spendable_epoch', 'bigint'),
        ('pool_id', 'bigint'),
        ],
    'withdrawal': [('id', 'bigint'), ('addr_id', 'bigint'), ('amount',
                   'lovelace'), ('redeemer_id', 'bigint'), ('tx_id',
                   'bigint')],
    'epoch_stake': [('id', 'bigint'), ('addr_id', 'bigint'), ('pool_id'
                    , 'bigint'), ('amount', 'lovelace'), ('epoch_no',
                    'word31type')],
    'ma_tx_mint': [('id', 'bigint'), ('quantity', 'int65type'), ('tx_id'
                   , 'bigint'), ('ident', 'bigint')],
    'treasury': [('id', 'bigint'), ('addr_id', 'bigint'), ('cert_index'
                 , 'integer'), ('amount', 'int65type'), ('tx_id',
                 'bigint')],
    'reserve': [('id', 'bigint'), ('addr_id', 'bigint'), ('cert_index',
                'integer'), ('amount', 'int65type'), ('tx_id', 'bigint'
                )],
    'pot_transfer': [('id', 'bigint'), ('cert_index', 'integer'),
                     ('treasury', 'int65type'), ('reserves', 'int65type'
                     ), ('tx_id', 'bigint')],
    'epoch_sync_time': [('id', 'bigint'), ('no', 'bigint'), ('seconds',
                        'word63type'), ('state', 'syncstatetype')],
    'ma_tx_out': [('id', 'bigint'), ('quantity', 'word64type'),
                  ('tx_out_id', 'bigint'), ('ident', 'bigint')],
    'script': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('hash', 'hash28type'),
        ('type', 'scripttype'),
        ('json', 'jsonb'),
        ('bytes', 'bytea'),
        ('serialised_size', 'word31type'),
        ],
    'pool_offline_data': [
        ('id', 'bigint'),
        ('pool_id', 'bigint'),
        ('ticker_name', 'character varying'),
        ('hash', 'hash32type'),
        ('json', 'jsonb'),
        ('bytes', 'bytea'),
        ('pmr_id', 'bigint'),
        ],
    'cost_model': [('id', 'bigint'), ('costs', 'jsonb'), ('hash',
                   'hash32type')],
    'param_proposal': [
        ('id', 'bigint'),
        ('epoch_no', 'word31type'),
        ('key', 'hash28type'),
        ('min_fee_a', 'word64type'),
        ('min_fee_b', 'word64type'),
        ('max_block_size', 'word64type'),
        ('max_tx_size', 'word64type'),
        ('max_bh_size', 'word64type'),
        ('key_deposit', 'lovelace'),
        ('pool_deposit', 'lovelace'),
        ('max_epoch', 'word64type'),
        ('optimal_pool_count', 'word64type'),
        ('influence', 'double precision'),
        ('monetary_expand_rate', 'double precision'),
        ('treasury_growth_rate', 'double precision'),
        ('decentralisation', 'double precision'),
        ('entropy', 'hash32type'),
        ('protocol_major', 'word31type'),
        ('protocol_minor', 'word31type'),
        ('min_utxo_value', 'lovelace'),
        ('min_pool_cost', 'lovelace'),
        ('cost_model_id', 'bigint'),
        ('price_mem', 'double precision'),
        ('price_step', 'double precision'),
        ('max_tx_ex_mem', 'word64type'),
        ('max_tx_ex_steps', 'word64type'),
        ('max_block_ex_mem', 'word64type'),
        ('max_block_ex_steps', 'word64type'),
        ('max_val_size', 'word64type'),
        ('collateral_percent', 'word31type'),
        ('max_collateral_inputs', 'word31type'),
        ('registered_tx_id', 'bigint'),
        ('coins_per_utxo_size', 'lovelace'),
        ],
    'epoch_param': [
        ('id', 'bigint'),
        ('epoch_no', 'word31type'),
        ('min_fee_a', 'word31type'),
        ('min_fee_b', 'word31type'),
        ('max_block_size', 'word31type'),
        ('max_tx_size', 'word31type'),
        ('max_bh_size', 'word31type'),
        ('key_deposit', 'lovelace'),
        ('pool_deposit', 'lovelace'),
        ('max_epoch', 'word31type'),
        ('optimal_pool_count', 'word31type'),
        ('influence', 'double precision'),
        ('monetary_expand_rate', 'double precision'),
        ('treasury_growth_rate', 'double precision'),
        ('decentralisation', 'double precision'),
        ('protocol_major', 'word31type'),
        ('protocol_minor', 'word31type'),
        ('min_utxo_value', 'lovelace'),
        ('min_pool_cost', 'lovelace'),
        ('nonce', 'hash32type'),
        ('cost_model_id', 'bigint'),
        ('price_mem', 'double precision'),
        ('price_step', 'double precision'),
        ('max_tx_ex_mem', 'word64type'),
        ('max_tx_ex_steps', 'word64type'),
        ('max_block_ex_mem', 'word64type'),
        ('max_block_ex_steps', 'word64type'),
        ('max_val_size', 'word64type'),
        ('collateral_percent', 'word31type'),
        ('max_collateral_inputs', 'word31type'),
        ('block_id', 'bigint'),
        ('extra_entropy', 'hash32type'),
        ('coins_per_utxo_size', 'lovelace'),
        ],
    'pool_offline_fetch_error': [
        ('id', 'bigint'),
        ('pool_id', 'bigint'),
        ('fetch_time', 'timestamp without time zone'),
        ('pmr_id', 'bigint'),
        ('fetch_error', 'character varying'),
        ('retry_count', 'word31type'),
        ],
    'multi_asset': [('id', 'bigint'), ('policy', 'hash28type'), ('name'
                    , 'asset32type'), ('fingerprint',
                    'character varying')],
    'meta': [('id', 'bigint'), ('start_time',
             'timestamp without time zone'), ('network_name',
             'character varying'), ('version', 'character varying')],
    'delisted_pool': [('id', 'bigint'), ('hash_raw', 'hash28type')],
    'reserved_pool_ticker': [('id', 'bigint'), ('name',
                             'character varying'), ('pool_hash',
                             'hash28type')],
    'extra_key_witness': [('id', 'bigint'), ('hash', 'hash28type'),
                          ('tx_id', 'bigint')],
    'reference_tx_in': [('id', 'bigint'), ('tx_in_id', 'bigint'),
                        ('tx_out_id', 'bigint'), ('tx_out_index',
                        'txindex')],
    'redeemer_data': [('id', 'bigint'), ('hash', 'hash32type'), ('tx_id'
                      , 'bigint'), ('value', 'jsonb'), ('bytes', 'bytea'
                      )],
    'collateral_tx_out': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('index', 'txindex'),
        ('address', 'character varying'),
        ('address_raw', 'bytea'),
        ('address_has_script', 'boolean'),
        ('payment_cred', 'hash28type'),
        ('stake_address_id', 'bigint'),
        ('value', 'lovelace'),
        ('data_hash', 'hash32type'),
        ('multi_assets_descr', 'character varying'),
        ('inline_datum_id', 'bigint'),
        ('reference_script_id', 'bigint'),
        ],
    'reverse_index': [('id', 'bigint'), ('block_id', 'bigint'),
                      ('min_ids', 'character varying')],
    'utxo_byron_view': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('index', 'txindex'),
        ('address', 'character varying'),
        ('address_raw', 'bytea'),
        ('address_has_script', 'boolean'),
        ('payment_cred', 'hash28type'),
        ('stake_address_id', 'bigint'),
        ('value', 'lovelace'),
        ('data_hash', 'hash32type'),
        ('inline_datum_id', 'bigint'),
        ('reference_script_id', 'bigint'),
        ],
    'utxo_view': [
        ('id', 'bigint'),
        ('tx_id', 'bigint'),
        ('index', 'txindex'),
        ('address', 'character varying'),
        ('address_raw', 'bytea'),
        ('address_has_script', 'boolean'),
        ('payment_cred', 'hash28type'),
        ('stake_address_id', 'bigint'),
        ('value', 'lovelace'),
        ('data_hash', 'hash32type'),
        ('inline_datum_id', 'bigint'),
        ('reference_script_id', 'bigint'),
        ],
    }

EXPECTED_DB_INDEXES = {
    'pool_metadata_ref': ['pool_metadata_ref_pkey',
                          'unique_pool_metadata_ref'],
    'pool_update': ['pool_update_pkey'],
    'pool_owner': ['pool_owner_pkey'],
    'pool_retire': ['pool_retire_pkey'],
    'ada_pots': ['ada_pots_pkey'],
    'pool_relay': ['pool_relay_pkey'],
    'schema_version': ['schema_version_pkey'],
    'pool_hash': ['pool_hash_pkey', 'unique_pool_hash'],
    'slot_leader': ['slot_leader_pkey', 'unique_slot_leader'],
    'block': ['block_pkey', 'unique_block'],
    'tx': ['tx_pkey', 'unique_tx'],
    'stake_address': ['stake_address_pkey', 'unique_stake_address'],
    'tx_out': ['tx_out_pkey', 'unique_txout'],
    'datum': ['datum_pkey', 'unique_datum'],
    'redeemer': ['redeemer_pkey'],
    'tx_in': ['tx_in_pkey'],
    'collateral_tx_in': ['collateral_tx_in_pkey'],
    'meta': ['meta_pkey', 'unique_meta'],
    'epoch': ['epoch_pkey', 'unique_epoch'],
    'stake_registration': ['stake_registration_pkey'],
    'stake_deregistration': ['stake_deregistration_pkey'],
    'tx_metadata': ['tx_metadata_pkey'],
    'delegation': ['delegation_pkey'],
    'reward': ['reward_pkey', 'unique_reward'],
    'withdrawal': ['withdrawal_pkey'],
    'epoch_stake': ['epoch_stake_pkey', 'unique_stake'],
    'treasury': ['treasury_pkey'],
    'reserve': ['reserve_pkey'],
    'pot_transfer': ['pot_transfer_pkey'],
    'epoch_sync_time': ['epoch_sync_time_pkey', 'unique_epoch_sync_time'
                        ],
    'ma_tx_mint': ['ma_tx_mint_pkey'],
    'ma_tx_out': ['ma_tx_out_pkey'],
    'script': ['script_pkey', 'unique_script'],
    'cost_model': ['cost_model_pkey', 'unique_cost_model'],
    'epoch_param': ['epoch_param_pkey'],
    'pool_offline_data': ['pool_offline_data_pkey',
                          'unique_pool_offline_data'],
    'param_proposal': ['param_proposal_pkey'],
    'pool_offline_fetch_error': ['pool_offline_fetch_error_pkey',
                                 'unique_pool_offline_fetch_error'],
    'multi_asset': ['multi_asset_pkey', 'unique_multi_asset'],
    'delisted_pool': ['delisted_pool_pkey', 'unique_delisted_pool'],
    'reserved_pool_ticker': ['reserved_pool_ticker_pkey',
                             'unique_reserved_pool_ticker'],
    'extra_key_witness': ['extra_key_witness_pkey'],
    'collateral_tx_out': ['collateral_tx_out_pkey'],
    'reference_tx_in': ['reference_tx_in_pkey'],
    'redeemer_data': ['redeemer_data_pkey', 'unique_redeemer_data'],
    'reverse_index': ['reverse_index_pkey'],
    }