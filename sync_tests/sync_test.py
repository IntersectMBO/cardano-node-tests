import argparse
import json
import os
import platform
import signal
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from psutil import process_iter

NODE = "./cardano-node"
CLI = "./cardano-cli"
ROOT_TEST_PATH = ""
CARDANO_NODE_PATH = ""
CARDANO_NODE_TESTS_PATH = ""


def set_repo_paths():
    global CARDANO_NODE_PATH
    global CARDANO_NODE_TESTS_PATH
    global ROOT_TEST_PATH

    ROOT_TEST_PATH = Path.cwd()

    os.chdir("cardano-node")
    CARDANO_NODE_PATH = Path.cwd()

    os.chdir("..")
    os.chdir("cardano-node-tests")
    CARDANO_NODE_TESTS_PATH = Path.cwd()
    os.chdir("..")


def git_get_last_pr_from_tag(tag_no):
    os.chdir(Path(CARDANO_NODE_PATH))
    cmd = (
            "git log --pretty=format:%s "
            + tag_no
            + " | grep Merge | head -n1"
    )
    try:
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        os.chdir(ROOT_TEST_PATH)

        return str(output.splitlines()[0].split(" #")[1])
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def set_node_socket_path_env_var():
    if "windows" in platform.system().lower():
        socket_path = "\\\\.\pipe\cardano-node"
    else:
        socket_path = (Path(CARDANO_NODE_TESTS_PATH) / "db" / "node.socket").expanduser().absolute()

    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)


def get_os_type():
    return [platform.system(), platform.release(), platform.version()]


def get_testnet_value():
    env = vars(args)["environment"]
    if env == "mainnet":
        return "--mainnet"
    elif env == "testnet":
        return "--testnet-magic 1097911063"
    elif env == "staging":
        return "--testnet-magic 633343913"
    else:
        return None


def wait_for_node_to_start():
    # when starting from clean state it might take ~30 secs for the cli to work
    # when starting from existing state it might take >5 mins for the cli to work (opening db and
    # replaying the ledger)
    tip = get_current_tip(True)
    count = 0
    while tip == 1:
        time.sleep(10)
        count += 1
        tip = get_current_tip(True)
        if count >= 540:  # 90 mins
            print(" **************  ERROR: waited 90 mins and CLI is still not usable ********* ")
            print(f"      TIP: {get_current_tip()}")
            exit(1)
    print(f"************** CLI became available after: {count * 10} seconds **************")
    return count * 10


def get_current_tip(wait=False):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    try:
        cmd = CLI + " shelley query tip " + get_testnet_value()
        output = (
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
                .decode("utf-8")
                .strip()
        )
        output_json = json.loads(output)
        return int(output_json["blockNo"]), output_json["headerHash"], int(output_json["slotNo"])
    except subprocess.CalledProcessError as e:
        if wait:
            return int(e.returncode)
        else:
            raise RuntimeError(
                "command '{}' return with error (code {}): {}".format(
                    e.cmd, e.returncode, " ".join(str(e.output).split())
                )
            )


def get_node_version():
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
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


def start_node_windows(env):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    current_directory = Path.cwd()
    cmd = \
        NODE + \
        " run --topology " + \
        env + \
        "-topology.json --database-path db --port 3000 --config " + \
        env + \
        "-config.json --socket-path \\\\.\pipe\cardano-node "
    logfile = open("logfile.log", "w+")
    print(f"cmd: {cmd}")

    try:
        p = subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.PIPE)
        print("waiting for db folder to be created")
        count = 0
        while not os.path.isdir(current_directory / "db"):
            time.sleep(3)
            count += 1
            if count > 9:
                print("ERROR: waited 30 seconds and the DB folder was not created yet")
                exit(1)

        secs_to_start = wait_for_node_to_start()
        print("DB folder was created")
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(current_directory / 'db')}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, ' '.join(str(e.output).split())))


def start_node_unix(env):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    current_directory = Path.cwd()
    cmd = (
        f"{NODE} run --topology {env}-topology.json --database-path "
        f"{Path(CARDANO_NODE_TESTS_PATH) / 'db'} "
        f"--host-addr 0.0.0.0 --port 3000 --config "
        f"{env}-config.json --socket-path ./db/node.socket"
    )

    logfile = open("logfile.log", "w+")
    print(f"cmd: {cmd}")

    try:
        subprocess.Popen(cmd.split(" "), stdout=logfile, stderr=subprocess.PIPE)
        print("waiting for db folder to be created")
        count = 0
        while not os.path.isdir(current_directory / "db"):
            time.sleep(3)
            count += 1
            if count > 10:
                print("ERROR: waited 30 seconds and the DB folder was not created yet")
                break

        secs_to_start = wait_for_node_to_start()
        print("DB folder was created")
        print(f" - listdir current_directory: {os.listdir(current_directory)}")
        print(f" - listdir db: {os.listdir(current_directory / 'db')}")
        return secs_to_start
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "command '{}' return with error (code {}): {}".format(
                e.cmd, e.returncode, " ".join(str(e.output).split())
            )
        )


def stop_node():
    for proc in process_iter():
        if "cardano-node" in proc.name():
            print(f" --- Killing the `cardano-node` process - {proc}")
            proc.send_signal(signal.SIGTERM)
            proc.terminate()
            proc.kill()
    time.sleep(10)
    for proc in process_iter():
        if "cardano-node" in proc.name():
            print(f" --- ERROR: `cardano-node` process is still active - {proc}")


def percentage(part, whole):
    return round(100 * float(part) / float(whole), 2)


def get_current_date_time():
    now = datetime.now()
    return now.strftime("%d/%m/%Y %H:%M:%S")


def get_file_creation_date(path_to_file):
    return time.ctime(os.path.getmtime(path_to_file))


def get_and_extract_linux_files(tag_no):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")

    pr_no = git_get_last_pr_from_tag(tag_no)
    archive_name = f"cardano-node-{tag_no}-linux.tar.gz"
    node_files = (
        f"https://hydra.iohk.io/job/Cardano/cardano-node-pr-"
        f"{pr_no}/cardano-node-linux/latest-finished"
        f"/download/1/{archive_name}"
    )

    print(f"pr_no       : {pr_no}")
    print(f"tag_no      : {tag_no}")
    print(f"archive_name: {archive_name}")
    print(f"node_files  : {node_files}")

    urllib.request.urlretrieve(node_files, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")

    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    print(f" - listdir (after archive extraction): {os.listdir(current_directory)}")


def get_and_extract_macos_files(tag_no):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")

    pr_no = git_get_last_pr_from_tag(tag_no)
    archive_name = f"cardano-node-{tag_no}-macos.tar.gz"
    node_files = (
        f"https://hydra.iohk.io/job/Cardano/cardano-node-pr-"
        f"{pr_no}/cardano-node-macos/latest-finished"
        f"/download/1/{archive_name}"
    )

    print(f"pr_no       : {pr_no}")
    print(f"tag_no      : {tag_no}")
    print(f"archive_name: {archive_name}")
    print(f"node_files  : {node_files}")

    urllib.request.urlretrieve(node_files, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")

    tf = tarfile.open(Path(current_directory) / archive_name)
    tf.extractall(Path(current_directory))
    print(f" - listdir (after archive extraction): {os.listdir(current_directory)}")


def get_and_extract_windows_files(tag_no):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    current_directory = os.getcwd()
    print(f" - current_directory: {current_directory}")

    pr_no = git_get_last_pr_from_tag(tag_no)
    archive_name = f"cardano-node-{tag_no}-win64.zip"
    node_files = (
        f"https://hydra.iohk.io/job/Cardano/cardano-node-pr-"
        f"{pr_no}/cardano-node-win64/latest-finished"
        f"/download/1/cardano-node-{archive_name}"
    )

    print(f"pr_no       : {pr_no}")
    print(f"tag_no      : {tag_no}")
    print(f"archive_name: {archive_name}")
    print(f"node_files  : {node_files}")

    urllib.request.urlretrieve(node_files, Path(current_directory) / archive_name)

    print(f" ------ listdir (before archive extraction): {os.listdir(current_directory)}")

    with zipfile.ZipFile(Path(current_directory) / archive_name, "r") as zip_ref:
        zip_ref.extractall(current_directory)
    print(f" ------ listdir (after archive extraction): {os.listdir(current_directory)}")


def get_and_extract_node_files(tag_no):
    print(" - get and extract the pre-built node files")
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    platform_system, platform_release, platform_version = get_os_type()
    if "linux" in platform_system.lower():
        get_and_extract_linux_files(tag_no)
    elif "darwin" in platform_system.lower():
        get_and_extract_macos_files(tag_no)
    elif "windows" in platform_system.lower():
        get_and_extract_windows_files(tag_no)


def delete_node_files():
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    for p in Path(".").glob("cardano-*"):
        print(f" === deleting file: {p}")
        p.unlink(missing_ok=True)


def get_node_config_files(env):
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
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
        + "-topology.json",
        env + "-topology.json",
        )


def wait_for_node_to_sync(env):
    sync_details_dict = OrderedDict()
    count = 0
    last_byron_slot_no, latest_slot_no = get_calculated_slot_no(env)
    actual_slot_no = get_current_tip()[2]
    start_sync = time.perf_counter()

    while actual_slot_no <= last_byron_slot_no:
        value_dict = {
            "actual_slot_not": actual_slot_no,
            "actual_sync_percent": percentage(actual_slot_no, latest_slot_no),
            "actual_date_time": get_current_date_time(),
        }
        sync_details_dict[count] = value_dict

        print(
            f"  - actual_slot_no (Byron era): "
            f"{actual_slot_no} - {percentage(actual_slot_no, latest_slot_no)} % --> "
            f"{get_current_date_time()}"
        )
        time.sleep(60)
        count += 1
        actual_slot_no = get_current_tip()[2]

    end_byron_sync = time.perf_counter()

    while actual_slot_no < latest_slot_no:
        value_dict = {
            "actual_slot_not": actual_slot_no,
            "actual_sync_percent": percentage(actual_slot_no, latest_slot_no),
            "actual_date_time": get_current_date_time(),
        }
        sync_details_dict[count] = value_dict

        print(
            f"  - actual_slot_no (Shelley era): "
            f"{actual_slot_no} - {percentage(actual_slot_no, latest_slot_no)} % --> "
            f"{get_current_date_time()}"
        )
        time.sleep(60)
        count += 1
        actual_slot_no = get_current_tip()[2]

    end_shelley_sync = time.perf_counter()

    byron_sync_time_seconds = int(end_byron_sync - start_sync)
    shelley_sync_time_seconds = int(end_shelley_sync - end_byron_sync)

    # include also the last value into the db/dict (100%)
    value_dict = {
        "actual_slot_not": actual_slot_no,
        "actual_sync_percent": percentage(actual_slot_no, latest_slot_no),
        "actual_date_time": get_current_date_time(),
    }
    sync_details_dict[count] = value_dict

    os.chdir(Path(CARDANO_NODE_TESTS_PATH) / "db" / "immutable")
    chunk_files = sorted(os.listdir(os.getcwd()), key=os.path.getmtime)
    newest_chunk = chunk_files[-1]
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))
    print(f"Sync done!; newest_chunk: {newest_chunk}")
    return newest_chunk, byron_sync_time_seconds, shelley_sync_time_seconds, sync_details_dict


def date_diff_in_seconds(dt2, dt1):
    timedelta = dt2 - dt1
    return timedelta.days * 24 * 3600 + timedelta.seconds


def get_calculated_slot_no(env):
    if env == "testnet":
        byron_start_time = datetime.strptime("2019-07-24 20:20:16", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-28 20:20:16", "%Y-%m-%d %H:%M:%S")
    elif env == "staging":
        byron_start_time = datetime.strptime("2017-09-26 18:23:33", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-08-01 18:23:33", "%Y-%m-%d %H:%M:%S")
    else:
        byron_start_time = datetime.strptime("2017-09-23 21:44:51", "%Y-%m-%d %H:%M:%S")
        shelley_start_time = datetime.strptime("2020-07-29 21:44:51", "%Y-%m-%d %H:%M:%S")

    current_time = datetime.utcnow()
    last_byron_slot_no = int(date_diff_in_seconds(shelley_start_time, byron_start_time) / 20)
    latest_shelley_slot_no = int(
        date_diff_in_seconds(current_time, shelley_start_time) + last_byron_slot_no
    )

    print("----------------------------------------------------------------")
    print(f"byron_start_time        : {byron_start_time}")
    print(f"shelley_start_time      : {shelley_start_time}")
    print(f"current_utc_time        : {current_time}")
    print(f"last_byron_slot_no      : {last_byron_slot_no}")
    print(f"latest_shelley_slot_no  : {latest_shelley_slot_no}")
    print("----------------------------------------------------------------")

    return last_byron_slot_no, latest_shelley_slot_no


def main():
    global NODE
    global CLI

    secs_to_start1, secs_to_start2 = 0, 0

    set_repo_paths()
    print(f"root_test_path          : {ROOT_TEST_PATH}")
    print(f"cardano_node_path       : {CARDANO_NODE_PATH}")
    print(f"cardano_node_tests_path : {CARDANO_NODE_TESTS_PATH}")

    env = vars(args)["environment"]
    print(f"env: {env}")

    set_node_socket_path_env_var()

    tag_no1 = str(vars(args)["tag_number1"]).strip()
    tag_no2 = str(vars(args)["tag_number2"]).strip()
    print(f"tag_no1: {tag_no1}")
    print(f"tag_no2: {tag_no2}")
    pr_no1 = str(git_get_last_pr_from_tag(tag_no1)).strip()
    if tag_no2 == "None":
        pr_no2 = "None"
    else:
        pr_no2 = git_get_last_pr_from_tag(tag_no2)
    print(f"pr_no1: {pr_no1}")
    print(f"pr_no2: {pr_no2}")

    platform_system, platform_release, platform_version = get_os_type()
    print(f"platform: {platform_system, platform_release, platform_version}")

    if "windows" in platform_system.lower():
        NODE = "cardano-node.exe"
        CLI = "cardano-cli.exe"

    print("move to 'CARDANO_NODE_TESTS_PATH'")
    os.chdir(Path(CARDANO_NODE_TESTS_PATH))

    print("get the required node files")
    get_node_config_files(env)

    print("===================================================================================")
    print(f"=========================== Start sync using tag_no1: {tag_no1} ==================")
    print("===================================================================================")
    get_and_extract_node_files(tag_no1)

    print(" --- node version ---")
    cardano_cli_version1, cardano_cli_git_rev1 = get_node_version()
    print(f"  - cardano_cli_version1: {cardano_cli_version1}")
    print(f"  - cardano_cli_git_rev1: {cardano_cli_git_rev1}")

    print(f"   ======================= Start node using tag_no1: {tag_no1} ====================")
    start_sync_time1 = get_current_date_time()
    if "linux" in platform_system.lower() or "darwin" in platform_system.lower():
        secs_to_start1 = start_node_unix(env)
    elif "windows" in platform_system.lower():
        secs_to_start1 = start_node_windows(env)

    print(" - waiting for the node to sync")
    (
        newest_chunk1,
        byron_sync_time_seconds1,
        shelley_sync_time_seconds1,
        sync_details_dict1,
    ) = wait_for_node_to_sync(env)

    end_sync_time1 = get_current_date_time()
    print(f"secs_to_start1            : {secs_to_start1}")
    print(f"start_sync_time1          : {start_sync_time1}")
    print(f"end_sync_time1            : {end_sync_time1}")
    print(f"byron_sync_time_seconds1  : {byron_sync_time_seconds1}")
    print(
        f"byron_sync_time1  : {time.strftime('%H:%M:%S', time.gmtime(byron_sync_time_seconds1))}"
    )
    print(f"shelley_sync_time_seconds1: {shelley_sync_time_seconds1}")
    print(
        f"shelley_sync_time1: {time.strftime('%H:%M:%S', time.gmtime(shelley_sync_time_seconds1))}"
    )

    latest_block_no1 = get_current_tip()[0]
    latest_slot_no1 = get_current_tip()[2]
    sync_speed_bps1 = int(
        latest_block_no1 / (byron_sync_time_seconds1 + shelley_sync_time_seconds1)
    )
    sync_speed_sps1 = int(latest_slot_no1 / (byron_sync_time_seconds1 + shelley_sync_time_seconds1))
    print(f"sync_speed_bps1   : {sync_speed_bps1}")
    print(f"sync_speed_sps1   : {sync_speed_sps1}")

    total_chunks1 = int(newest_chunk1.split(".")[0])
    print(f"downloaded chunks1: {total_chunks1}")

    (
        cardano_cli_version2,
        cardano_cli_git_rev2,
        shelley_sync_time_seconds2,
        total_chunks2,
        latest_block_no2,
        latest_slot_no2,
        start_sync_time2,
        end_sync_time2,
    ) = (None, None, None, None, None, None, None, None)
    if tag_no2 != "None":
        print(f"   =============== Stop node using tag_no1: {tag_no1} ======================")
        stop_node()

        print("   ================ Delete the previous node files =======================")
        delete_node_files()

        print("==============================================================================")
        print(f"===================== Start sync using tag_no2: {tag_no2} ===================")
        print("==============================================================================")
        get_and_extract_node_files(tag_no2)

        print(" --- node version ---")
        cardano_cli_version2, cardano_cli_git_rev2 = get_node_version()
        print(f"  - cardano_cli_version2: {cardano_cli_version2}")
        print(f"  - cardano_cli_git_rev2: {cardano_cli_git_rev2}")
        print(f"   ================ Start node using tag_no2: {tag_no2} ====================")
        start_sync_time2 = get_current_date_time()
        if "linux" in platform_system.lower() or "darwin" in platform_system.lower():
            secs_to_start2 = start_node_unix(env)
        elif "windows" in platform_system.lower():
            secs_to_start2 = start_node_windows(env)

        print(f" - waiting for the node to sync - using tag_no2: {tag_no2}")
        (
            newest_chunk2,
            byron_sync_time_seconds2,
            shelley_sync_time_seconds2,
            sync_details_dict2,
        ) = wait_for_node_to_sync(env)

        end_sync_time2 = get_current_date_time()
        print(f"secs_to_start2    : {secs_to_start2}   = ledger revalidation time")
        print(f"start_sync_time2  : {start_sync_time2}")
        print(f"end_sync_time2    : {end_sync_time2}")
        print(f"byron_sync_time_seconds2  : {byron_sync_time_seconds2}")
        print(f"byron_sync_time2  : "
              f"{time.strftime('%H:%M:%S', time.gmtime(byron_sync_time_seconds2))}")
        print(f"shelley_sync_time_seconds2: {shelley_sync_time_seconds2}")
        print(f"shelley_sync_time2: "
              f"{time.strftime('%H:%M:%S', time.gmtime(shelley_sync_time_seconds2))}")

        latest_block_no2 = get_current_tip()[0]
        latest_slot_no2 = get_current_tip()[2]
        sync_speed_bps2 = int(
            (latest_block_no2 - latest_block_no1)
            / (byron_sync_time_seconds2 + shelley_sync_time_seconds2)
        )
        sync_speed_sps2 = int(
            (latest_slot_no2 - latest_slot_no1)
            / (byron_sync_time_seconds2 + shelley_sync_time_seconds2)
        )
        print(f"sync_speed_bps2   : {sync_speed_bps2}")
        print(f"sync_speed_sps2   : {sync_speed_sps2}")

        total_chunks2 = int(newest_chunk1.split(".")[0])
        print(f"downloaded chunks2: {total_chunks2}")

    print("move to 'cardano_node_tests_path/scripts'")
    os.chdir(Path(CARDANO_NODE_TESTS_PATH) / "sync_tests")
    current_directory = Path.cwd()
    print(f" - sync_tests listdir: {os.listdir(current_directory)}")

    test_values = (
        env,
        tag_no1,
        tag_no2,
        pr_no1,
        pr_no2,
        cardano_cli_version1,
        cardano_cli_version2,
        cardano_cli_git_rev1,
        cardano_cli_git_rev2,
        start_sync_time1,
        end_sync_time1,
        start_sync_time2,
        end_sync_time2,
        byron_sync_time_seconds1,
        shelley_sync_time_seconds1,
        shelley_sync_time_seconds2,
        total_chunks1,
        total_chunks2,
        latest_block_no1,
        latest_block_no2,
        latest_slot_no1,
        latest_slot_no2,
        secs_to_start1,
        secs_to_start2,
        platform_system,
        platform_release,
        platform_version,
        json.dumps(sync_details_dict1),
    )

    print(f"test_values: {test_values}")

    with open("sync_results.log", "w+") as file1:
        file1.write(str(test_values))

    current_directory = Path.cwd()
    print(f" - sync_tests listdir: {os.listdir(current_directory)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Execute basic sync test\n\n")

    parser.add_argument(
        "-t1", "--tag_number1", help="tag number1 - used for initial sync, from clean state"
    )
    parser.add_argument(
        "-t2", "--tag_number2", help="tag number2 - used for final sync, from existing state"
    )
    parser.add_argument(
        "-e",
        "--environment",
        help="the environment on which to run the tests - staging or mainnet.",
    )

    args = parser.parse_args()

    main()
