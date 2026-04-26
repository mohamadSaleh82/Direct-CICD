import os
import json
import time
import logging
import paramiko
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("deploy.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        exit(1)

config = load_config()
LOCAL_DIR = config["paths"]["local_path"]
REMOTE_DIR = config["paths"]["remote_path"]
IGNORE_LIST = config.get("ignore", [])
POST_SYNC_COMMAND = config.get("post_sync_command", "")

class SSHManager:
    def __init__(self, server_config):
        self.config = server_config
        self.ssh = None
        self.sftp = None
        self.connect()

    def connect(self):
        try:
            logger.info(f"Connecting to {self.config['host']}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.config["host"],
                port=self.config["port"],
                username=self.config["username"],
                password=self.config["password"],
                timeout=15
            )
            self.sftp = self.ssh.open_sftp()
            logger.info("Connected successfully!")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.ssh = None
            self.sftp = None

    def ensure_connection(self):
        if self.sftp is None or self.ssh is None or not self.ssh.get_transport().is_active():
            logger.warning("Connection lost or not established. Reconnecting...")
            self.connect()
        else:
            try:
                self.sftp.listdir(".")
            except:
                logger.warning("SFTP session stale. Reconnecting...")
                self.connect()

    def upload_file(self, local_path, remote_path):
        self.ensure_connection()
        if not self.sftp: return
        
        try:
            # Create remote directory if it doesn't exist
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            self.remote_mkdir_p(remote_dir)
            
            logger.info(f"Uploading: {os.path.basename(local_path)}")
            self.sftp.put(local_path, remote_path)
            return True
        except Exception as e:
            logger.error(f"Upload failed for {local_path}: {e}")
            return False

    def remove_file(self, remote_path):
        self.ensure_connection()
        if not self.sftp: return
        try:
            logger.info(f"Deleting remote: {remote_path}")
            self.sftp.remove(remote_path)
        except:
            # Might be a directory or already deleted
            try:
                self.sftp.rmdir(remote_path)
            except:
                pass

    def run_command(self, command):
        self.ensure_connection()
        if not self.ssh: return
        try:
            logger.info(f"Executing remote command: {command}")
            stdin, stdout, stderr = self.ssh.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                logger.info("Command executed successfully.")
            else:
                logger.error(f"Command failed with status {exit_status}")
                logger.error(stderr.read().decode())
        except Exception as e:
            logger.error(f"Failed to execute command: {e}")

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
                try:
                    self.sftp.mkdir(current_dir)
                    self.sftp.chdir(current_dir)
                except:
                    pass

class DeployHandler(FileSystemEventHandler):
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager
        self.last_sync = 0
        self.debounce_seconds = 0.5 # Wait a bit for file lock/multiple writes

    def on_modified(self, event):
        if not event.is_directory:
            self.sync_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self.sync_file(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self.delete_remote(event.src_path)

    def on_moved(self, event):
        self.delete_remote(event.src_path)
        self.sync_file(event.dest_path)

    def should_ignore(self, path):
        # Normalize path for better comparison
        path = path.replace("\\", "/")
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
        
        # Simple debounce to prevent double-firing or partial writes
        time.sleep(self.debounce_seconds)
        
        remote_path = self.get_remote_path(local_path)
        if self.ssh.upload_file(local_path, remote_path):
            if POST_SYNC_COMMAND:
                self.ssh.run_command(POST_SYNC_COMMAND)

    def delete_remote(self, local_path):
        if self.should_ignore(local_path):
            return
        remote_path = self.get_remote_path(local_path)
        self.ssh.remove_file(remote_path)

def full_sync(ssh_manager):
    logger.info("Performing initial full sync...")
    count = 0
    for root, dirs, files in os.walk(LOCAL_DIR):
        # Filter directories to ignore
        dirs[:] = [d for d in dirs if not any(ignore in os.path.join(root, d).replace("\\", "/") for ignore in IGNORE_LIST)]
        
        for file in files:
            local_path = os.path.join(root, file)
            if any(ignore in local_path.replace("\\", "/") for ignore in IGNORE_LIST):
                continue
            
            relative_path = os.path.relpath(local_path, LOCAL_DIR)
            remote_path = os.path.join(REMOTE_DIR, relative_path).replace("\\", "/")
            if ssh_manager.upload_file(local_path, remote_path):
                count += 1
    
    logger.info(f"Full sync completed. {count} files uploaded.")
    if count > 0 and POST_SYNC_COMMAND:
        ssh_manager.run_command(POST_SYNC_COMMAND)

if __name__ == "__main__":
    print("-" * 50)
    print("      DIRECT-DEPLOY (CI/CD TOOL)")
    print("-" * 50)
    
    if not os.path.exists(LOCAL_DIR):
        logger.warning(f"Local directory {LOCAL_DIR} does not exist. Creating it...")
        os.makedirs(LOCAL_DIR)

    ssh_manager = SSHManager(config["server"])
    
    # Optional: Initial sync - you can uncomment this or pass a flag
    # full_sync(ssh_manager)

    event_handler = DeployHandler(ssh_manager)
    observer = Observer()
    observer.schedule(event_handler, LOCAL_DIR, recursive=True)
    
    logger.info(f"Monitoring: {LOCAL_DIR}")
    logger.info(f"Target: {REMOTE_DIR}")
    logger.info("Ready! Press Ctrl+C to exit.")
    
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping observer...")
        observer.stop()
    observer.join()
    logger.info("Goodbye!")
