import os
import platform
import zipfile
import signal
import subprocess
import json

from os.path import normpath, basename
from pathlib import Path
from psutil import process_iter
from datetime import datetime
from git import Repo

import psutil
import time


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


def upload_artifact(file):
    p = subprocess.Popen(["buildkite-agent", "artifact", "upload", f"{file}"])
    outs, errs = p.communicate(timeout=180)
    if outs is not None: print(outs)


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
