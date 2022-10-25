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

from os.path import normpath, basename
from pathlib import Path
from psutil import process_iter
from datetime import datetime
from git import Repo

import psutil
import time


ONE_MINUTE = 60
ROOT_TEST_PATH = Path.cwd()

POSTGRES_DIR = ROOT_TEST_PATH.parents[0]
POSTGRES_USER = subprocess.run(['whoami'], stdout=subprocess.PIPE).stdout.decode('utf-8').strip()

db_sync_perf_stats = []
DB_SYNC_PERF_STATS_FILE_NAME = "db_sync_performance_stats.json"
DB_SYNC_PERF_STATS_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/{DB_SYNC_PERF_STATS_FILE_NAME}"

NODE_LOG_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-node/node_logfile.log"
DB_SYNC_LOG_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/db_sync_logfile.log"
EPOCH_SYNC_TIMES_FILE_NAME = 'epoch_sync_times_dump.json'
EPOCH_SYNC_TIMES_FILE_PATH = f"{ROOT_TEST_PATH}/cardano-db-sync/{EPOCH_SYNC_TIMES_FILE_NAME}"

NODE_ARCHIVE = 'cardano_node.zip'
DB_SYNC_ARCHIVE = 'cardano_db_sync.zip'
SYNC_DATA_ARCHIVE = 'epoch_sync_times_dump.zip'
PERF_STATS_ARCHIVE = 'db_sync_perf_stats.zip'


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


def export_env_var(name, value):
    os.environ[name] = str(value)


def wait(seconds):
    time.sleep(seconds)


def clone_repo(repo_name, repo_branch):
    location = os.getcwd() + f"/{repo_name}"
    repo = Repo.clone_from(f"git@github.com:input-output-hk/{repo_name}.git", location)
    repo.git.checkout(repo_branch)
    print(f"Repo: {repo_name} cloned to: {location}")
    return location


def clone_repo_fork(repo_name, repo_branch):
    location = os.getcwd() + f"/{repo_name}"
    repo = Repo.clone_from(f"git@github.com:ArturWieczorek/{repo_name}.git", location)
    repo.git.checkout(repo_branch)
    print(f"Repo: {repo_name} cloned to: {location}")
    return location


def make_tarfile(output_filename, source_dir):
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))


def upload_node_database(env):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    os.chdir(Path.cwd() / 'cardano-node')
    node_db_archive = f"db-{env}.tar.gz"
    make_tarfile(node_db_archive, "db")
    upload_artifact(node_db_archive)
    os.chdir(current_directory)

    
def upload_artifact(file):
    p = subprocess.Popen(["buildkite-agent", "artifact", "upload", f"{file}"])
    outs, errs = p.communicate(timeout=180)
    if outs is not None: print(outs)


def set_build_meta_data(key, value):
    p = subprocess.Popen(["buildkite-agent", "meta-data", "set", f"{key}", f"{value}"])
    outs, errs = p.communicate(timeout=5)


def get_build_meta_data(key):
    p = subprocess.Popen(["buildkite-agent", "meta-data", "get", f"{key}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    outs, errs = p.communicate(timeout=5)
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
                print(line)
            else: break
    else: print(contents)


def get_process_info(proc_name):
    for proc in process_iter():
        if proc_name in proc.name():
            return proc


def stop_process(proc_name):
    for proc in process_iter():
        if proc_name in proc.name():
            print(f" --- Killing the {proc_name} process - {proc}")
            proc.send_signal(signal.SIGTERM)
            proc.terminate()
            proc.kill()
    time.sleep(30)
    for proc in process_iter():
        if proc_name in proc.name():
            print(f" !!! ERROR: {proc_name} process is still active - {proc}")


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
        print(line)
    print("")


def get_last_perf_stats_point():
    try:
        last_perf_stats_point = db_sync_perf_stats[-1]
    except:
        db_sync_perf_stats.append({"cpu_percent_usage": 0}, {"rss_mem_usage": 0})
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
    p = subprocess.Popen(["psql", f"{env}", "-t", "-c", f"\o {file}", "-c", f"SELECT array_to_json(array_agg(epoch_sync_time), FALSE) FROM epoch_sync_time where no >= {snapshot_epoch_no};" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        outs, errs = p.communicate(timeout=5)
        print(errs.decode("utf-8"))
        return outs.decode("utf-8")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise


def emergency_upload_artifacts(env):
    stop_process('cardano-db-sync')
    stop_process('cardano-node')

    write_data_as_json_to_file(DB_SYNC_PERF_STATS_FILE_NAME, db_sync_perf_stats)
    export_epoch_sync_times_from_db(env, EPOCH_SYNC_TIMES_FILE_NAME)

    zip_file(PERF_STATS_ARCHIVE, DB_SYNC_PERF_STATS_FILE_PATH)
    zip_file(SYNC_DATA_ARCHIVE, EPOCH_SYNC_TIMES_FILE_PATH)
    zip_file(DB_SYNC_ARCHIVE, DB_SYNC_LOG_FILE_PATH)
    zip_file(NODE_ARCHIVE, NODE_LOG_FILE_PATH)

    upload_artifact(PERF_STATS_ARCHIVE)
    upload_artifact(SYNC_DATA_ARCHIVE)
    upload_artifact(DB_SYNC_ARCHIVE)
    upload_artifact(NODE_ARCHIVE)


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
    urllib.request.urlretrieve(base_url + env + "/topology.json", env + "-topology.json",)


def get_node_version():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH / "cardano-node")
    try:
        cmd = "./cardano-cli --version"
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

    with requests.get(snapshot_url, headers = headers, stream = True, timeout = 1800) as r:
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
    cmd = "./cardano-cli query tip " + get_testnet_value(env)

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
                exit(1)
            pass
        time.sleep(ONE_MINUTE)
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
        cmd = f"scripts/postgresql-setup.sh --createdb"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
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


def build_db_sync():
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    db_sync_dir = Path.cwd() / 'cardano-db-sync'
    os.chdir(db_sync_dir)

    try:
        cmd = "nix-build -A cardano-db-sync -o db-sync-node"
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=2700)
            .decode("utf-8")
            .strip()
        )
        os.chdir(current_directory)
        print(f"build db-sync output: {output}")

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
        cmd = "db-sync-node/bin/cardano-db-sync --version"
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

    with requests.get(snapshot_url, headers = headers, stream = True, timeout = 60 * 30) as r:
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

    cmd = f"{Path.cwd().parent}/db-sync-tools/cardano-db-tool prepare-snapshot --state-dir ledger-state/{env}"
    p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        outs, errs = p.communicate(input="Y".encode("ascii"), timeout=600)
        if errs:
            error_msg = str(errs.decode("utf-8").strip())
            print(f"Errors: {error_msg}")
        final_line_with_script_cmd = str(outs.decode("utf-8")).split("\n")[5].lstrip()
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
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        outs, errs = p.communicate(timeout=43200) # 12 hours
        result = str(outs.decode("utf-8")).strip()
        print(f"Snapshot Creation - Stage 2 result: {result}")
        if errs:
            error_msg = str(errs.decode("utf-8").strip())
            print(f"Errors: {error_msg}")
        return str(outs.decode("utf-8")).split("\n")[3].lstrip()
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
            outs, errs = p.communicate(timeout=5)
            output_string = outs.decode("utf-8")
            epoch_no, block_no, slot_no = [e.strip() for e in outs.decode("utf-8").split("|")]
            return epoch_no, block_no, slot_no
        except ValueError as e:
            if counter > 15:
                should_try = False
                emergency_upload_artifacts(env)
                raise
            print(f"db-sync tip data unavailable, possible postgress failure. Output from psql: {output_string}")
            counter += 1
            print(e)
            print(errs)
            time.sleep(ONE_MINUTE)

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            p.kill()
            raise


def get_db_sync_progress(env):
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{env}",  "-c", "select 100 * (extract (epoch from (max (time) at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) / (extract (epoch from (now () at time zone 'UTC')) - extract (epoch from (min (time) at time zone 'UTC'))) as sync_percent from block ;" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
                emergency_upload_artifacts(env)
                raise
            print(f"db-sync progress unavailable, possible postgress failure. Output from psql: {progress_string}")
            counter += 1
            time.sleep(ONE_MINUTE)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            p.kill()
            raise


def wait_for_db_to_sync(env, sync_percentage = 99.9):
    db_sync_perf_stats.clear()
    start_sync = time.perf_counter()
    last_rollback_time = time.perf_counter()
    db_sync_progress = get_db_sync_progress(env)
    buildkite_timeout_in_sec = int(os.getenv('BUILDKITE_TIMEOUT')) * 60
    counter = 0
    rollback_counter = 0
    db_sync_process = get_process_info('cardano-db-sync')
    log_frequency = get_log_output_frequency(env)

    print("--- Db sync monitoring")
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
                print_n_last_lines_from_file(10, DB_SYNC_LOG_FILE_PATH)
                if time.perf_counter() - last_rollback_time > 10 * ONE_MINUTE:
                    print("Resetting previous rollback counter as there was no progress decrease for more than 10 minutes")
                    rollback_counter = 0
                last_rollback_time = time.perf_counter()
                rollback_counter += 1
                print(f"Rollback counter: {rollback_counter} out of 15")
            if rollback_counter > 15:
                print(f"Progress decreasing for {rollback_counter * counter} minutes.")
                print(f"Shutting down all services and emergency uploading artifacts")
                emergency_upload_artifacts(env)
                raise Exception('Rollback taking too long. Shutting down...')
        if counter % log_frequency == 0:
            node_epoch_no, node_block_no, node_hash, node_slot, node_era, node_sync_progress = get_node_tip(env)
            print(f"node progress [%]: {node_sync_progress}, epoch: {node_epoch_no}, block: {node_block_no}, slot: {node_slot}, era: {node_era}")
            epoch_no, block_no, slot_no = get_db_sync_tip(env)
            db_sync_progress = get_db_sync_progress(env)
            sync_time_h_m_s = seconds_to_time(time.perf_counter() - start_sync)
            print(f"db sync progress [%]: {db_sync_progress}, sync time [h:m:s]: {sync_time_h_m_s}, epoch: {epoch_no}, block: {block_no}, slot: {slot_no}")
            print_n_last_lines_from_file(5, DB_SYNC_LOG_FILE_PATH)

        try:
            time_point = int(time.perf_counter() - start_sync)
            _, _, slot_no = get_db_sync_tip(env)
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
    print(f"db sync progress [%] before finalizing process: {db_sync_progress}")
    return sync_time_seconds


def get_total_db_size(env):
    os.chdir(ROOT_TEST_PATH / "cardano-db-sync")
    p = subprocess.Popen(["psql", "-P", "pager=off", "-qt", "-U", f"{POSTGRES_USER}", "-d", f"{env}", "-c", f"SELECT pg_size_pretty( pg_database_size('{env}') );" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        outs, errs = p.communicate(timeout=5)
        print(errs.decode("utf-8"))
        return outs.decode("utf-8").rstrip().strip()
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise


def start_db_sync(env, start_args="", first_start="True"):
    current_directory = os.getcwd()
    os.chdir(ROOT_TEST_PATH)
    export_env_var("DB_SYNC_START_ARGS", start_args)
    export_env_var("FIRST_START", f"{first_start}")
    export_env_var("ENVIRONMENT", env)
    export_env_var("LOG_FILEPATH", DB_SYNC_LOG_FILE_PATH)

    try:
        cmd = "./scripts/db-sync-start.sh"
        p = subprocess.Popen(cmd, stderr=subprocess.STDOUT)
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


def get_db_sync_snaphot_size(snapshot_file):
    file_stats = os.stat(snapshot_file)
    snapshot_file_size_in_mb = int(file_stats.st_size / (1000 * 1000))
    return snapshot_file_size_in_mb


def is_string_present_in_db_sync_logs(log_file, search_string):
    encoded_search_string = str.encode(search_string)
    with open(log_file, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        if s.find(encoded_search_string) != -1:
            s.seek(s.find(encoded_search_string))
            print(s.readline().decode("utf-8"))
            return "Yes"
        return "No"


def are_errors_present_in_db_sync_logs(log_file):
    return is_string_present_in_db_sync_logs(log_file, "db-sync-node:Error")


def are_rollbacks_present_in_db_sync_logs(log_file):
    with open(log_file, 'rb', 0) as file, \
        mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as s:
        initial_rollback_position = s.find(b'Rolling')
        offset = s.find(b'Rolling', initial_rollback_position + len('Rolling'))
        if offset != -1:
            s.seek(offset)
            if s.find(b'Rolling'):
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
        cmd = f"./scripts/postgres-start.sh {pg_dir} -k"
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


def list_databases():
    p = subprocess.Popen(["psql", "-U", f"{POSTGRES_USER}", "-l" ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        outs, errs = p.communicate(timeout=5)
        output = outs.decode("utf-8")
        errors = errs.decode("utf-8")
        print(f"List databases: {output}")
        if errors:
            print(f"Error in list databases: {errors}")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        p.kill()
        raise
