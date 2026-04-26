import os
import json
import time
import paramiko
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

config = load_config()
LOCAL_DIR = config["paths"]["local_path"]
REMOTE_DIR = config["paths"]["remote_path"]
IGNORE_LIST = config.get("ignore", [])

class SSHManager:
    def __init__(self, server_config):
        self.config = server_config
        self.ssh = None
        self.sftp = None
        self.connect()

    def connect(self):
        try:
            print(f"[*] Connecting to {self.config['host']}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.config["host"],
                port=self.config["port"],
                username=self.config["username"],
                password=self.config["password"],
                timeout=10
            )
            self.sftp = self.ssh.open_sftp()
            print("[+] Connected successfully!")
        except Exception as e:
            print(f"[-] Connection failed: {e}")
            self.ssh = None
            self.sftp = None

    def ensure_connection(self):
        if self.sftp is None:
            self.connect()
        else:
            try:
                self.sftp.listdir(".")
            except:
                print("[!] Connection lost. Reconnecting...")
                self.connect()

    def upload_file(self, local_path, remote_path):
        self.ensure_connection()
        if not self.sftp: return
        
        try:
            # Create remote directory if it doesn't exist
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            self.remote_mkdir_p(remote_dir)
            
            print(f"[/] Uploading: {local_path} -> {remote_path}")
            self.sftp.put(local_path, remote_path)
        except Exception as e:
            print(f"[-] Upload failed for {local_path}: {e}")

    def remove_file(self, remote_path):
        self.ensure_connection()
        if not self.sftp: return
        try:
            print(f"[-] Deleting remote: {remote_path}")
            self.sftp.remove(remote_path)
        except:
            # Might be a directory or already deleted
            try:
                self.sftp.rmdir(remote_path)
            except:
                pass

    def remote_mkdir_p(self, remote_directory):
        """Equivalent of mkdir -p on remote"""
        dirs = remote_directory.split('/')
        current_dir = ""
        for d in dirs:
            if not d: continue
            current_dir += "/" + d
            try:
                self.sftp.chdir(current_dir)
            except IOError:
                self.sftp.mkdir(current_dir)
                self.sftp.chdir(current_dir)

class DeployHandler(FileSystemEventHandler):
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager

    def on_modified(self, event):
        if not event.is_directory:
            self.sync_file(event.src_path)

    def on_created(self, event):
        self.sync_file(event.src_path)

    def on_deleted(self, event):
        self.delete_remote(event.src_path)

    def on_moved(self, event):
        self.delete_remote(event.src_path)
        self.sync_file(event.dest_path)

    def should_ignore(self, path):
        for ignore_item in IGNORE_LIST:
            if ignore_item in path:
                return True
        return False

    def get_remote_path(self, local_path):
        relative_path = os.path.relpath(local_path, LOCAL_DIR)
        return os.path.join(REMOTE_DIR, relative_path).replace("\\", "/")

    def sync_file(self, local_path):
        if self.should_ignore(local_path):
            return
        remote_path = self.get_remote_path(local_path)
        self.ssh.upload_file(local_path, remote_path)

    def delete_remote(self, local_path):
        if self.should_ignore(local_path):
            return
        remote_path = self.get_remote_path(local_path)
        self.ssh.remove_file(remote_path)

def full_sync(ssh_manager):
    print("[*] Performing initial full sync...")
    for root, dirs, files in os.walk(LOCAL_DIR):
        # Filter directories to ignore
        dirs[:] = [d for d in dirs if not any(ignore in os.path.join(root, d) for ignore in IGNORE_LIST)]
        
        for file in files:
            local_path = os.path.join(root, file)
            if any(ignore in local_path for ignore in IGNORE_LIST):
                continue
            
            relative_path = os.path.relpath(local_path, LOCAL_DIR)
            remote_path = os.path.join(REMOTE_DIR, relative_path).replace("\\", "/")
            ssh_manager.upload_file(local_path, remote_path)
    print("[+] Full sync completed.")

if __name__ == "__main__":
    if not os.path.exists(LOCAL_DIR):
        print(f"[!] Local directory {LOCAL_DIR} does not exist. Creating it...")
        os.makedirs(LOCAL_DIR)

    ssh_manager = SSHManager(config["server"])
    
    # Optional: Initial sync
    # full_sync(ssh_manager)

    event_handler = DeployHandler(ssh_manager)
    observer = Observer()
    observer.schedule(event_handler, LOCAL_DIR, recursive=True)
    
    print(f"\n[!] Monitoring changes in: {LOCAL_DIR}")
    print("[!] Target Server: {REMOTE_DIR}")
    print("[!] Press Ctrl+C to stop.\n")
    
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
