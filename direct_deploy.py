import os
import json
import time
import logging
import hashlib
import fnmatch
import argparse
import paramiko
import zlib
import urllib.request
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

# Argument Parsing
parser = argparse.ArgumentParser(description="Direct-Deploy CI/CD Tool")
parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
parser.add_argument("--profile", type=str, default="default", help="Select server profile from config")
parser.add_argument("--full-sync", action="store_true", help="Perform a full sync on startup")
args = parser.parse_args()

# Load configuration
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
            # Support for profiles
            if "profiles" in data:
                return data["profiles"].get(args.profile, data["profiles"].get("default", data))
            return data
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        exit(1)

config = load_config()
LOCAL_DIR = config["paths"]["local_path"]
REMOTE_DIR = config["paths"]["remote_path"]
IGNORE_LIST = config.get("ignore", [])
POST_SYNC_COMMAND = config.get("post_sync_command", "")
PRE_SYNC_COMMAND = config.get("pre_sync_command", "")
HEALTH_CHECK_URL = config.get("health_check_url", "")
ENABLE_BACKUP = config.get("enable_backup", False)
ENABLE_COMPRESSION = config.get("enable_compression", True)
MIRROR_REMOTE = config.get("mirror_remote", False)

# File hashes to prevent redundant uploads
file_hashes = {}

def get_file_hash(filepath):
    """Calculate MD5 hash of a file."""
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except:
        return None

class SSHManager:
    def __init__(self, server_config):
        self.config = server_config
        self.ssh = None
        self.sftp = None
        self.connect()

    def connect(self):
        if args.dry_run: return
        try:
            logger.info(f"Connecting to {self.config['host']}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_params = {
                "hostname": self.config["host"],
                "port": self.config["port"],
                "username": self.config["username"],
                "timeout": 15
            }
            
            # Feature 1: SSH Key Support
            if self.config.get("key_path"):
                connect_params["key_filename"] = self.config["key_path"]
            else:
                connect_params["password"] = self.config["password"]
                
            self.ssh.connect(**connect_params)
            self.sftp = self.ssh.open_sftp()
            logger.info("Connected successfully!")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.ssh = None
            self.sftp = None

    def ensure_connection(self):
        if args.dry_run: return True
        if self.sftp is None or self.ssh is None or not self.ssh.get_transport().is_active():
            self.connect()
        else:
            try:
                self.sftp.listdir(".")
            except:
                self.connect()
        return self.sftp is not None

    def upload_file(self, local_path, remote_path):
        if not self.ensure_connection(): return False
        
        # Feature 2: Dry Run Check
        if args.dry_run:
            logger.info(f"[DRY-RUN] Would upload: {os.path.basename(local_path)}")
            return True

        try:
            # Feature 3: Backup before upload
            if ENABLE_BACKUP:
                try:
                    self.sftp.rename(remote_path, remote_path + ".bak")
                except:
                    pass

            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            self.remote_mkdir_p(remote_dir)
            
            logger.info(f"Uploading: {os.path.basename(local_path)}")
            
            # Feature 4: Compression Support
            if ENABLE_COMPRESSION and os.path.getsize(local_path) > 1024: # > 1KB
                with open(local_path, 'rb') as f:
                    data = f.read()
                compressed = zlib.compress(data)
                # Note: SFTP doesn't support decompressing on fly, so we upload normally
                # but we could use SSH for compressed streams. For now, standard upload.
                self.sftp.put(local_path, remote_path)
            else:
                self.sftp.put(local_path, remote_path)
                
            return True
        except Exception as e:
            logger.error(f"Upload failed for {local_path}: {e}")
            return False

    def remove_file(self, remote_path):
        if not self.ensure_connection(): return
        if args.dry_run:
            logger.info(f"[DRY-RUN] Would delete: {remote_path}")
            return
        try:
            logger.info(f"Deleting remote: {remote_path}")
            self.sftp.remove(remote_path)
        except:
            try:
                self.sftp.rmdir(remote_path)
            except:
                pass

    def run_command(self, command):
        if not self.ensure_connection(): return
        if args.dry_run:
            logger.info(f"[DRY-RUN] Would run: {command}")
            return
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
        if args.dry_run: return
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
                except:
                    pass

class DeployHandler(FileSystemEventHandler):
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager
        self.debounce_seconds = 0.5 

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
        # Feature 5: Glob Pattern Support
        path = path.replace("\\", "/")
        for pattern in IGNORE_LIST:
            if fnmatch.fnmatch(path, f"*{pattern}*"):
                return True
        return False

    def get_remote_path(self, local_path):
        relative_path = os.path.relpath(local_path, LOCAL_DIR)
        return os.path.join(REMOTE_DIR, relative_path).replace("\\", "/")

    def sync_file(self, local_path):
        if self.should_ignore(local_path):
            return
        
        # Feature 6: Content Hashing (Avoid redundant uploads)
        new_hash = get_file_hash(local_path)
        if local_path in file_hashes and file_hashes[local_path] == new_hash:
            return
        
        time.sleep(self.debounce_seconds)
        
        # Feature 7: Pre-sync Local Command
        if PRE_SYNC_COMMAND:
            logger.info(f"Running local pre-sync command: {PRE_SYNC_COMMAND}")
            os.system(PRE_SYNC_COMMAND)

        remote_path = self.get_remote_path(local_path)
        if self.ssh.upload_file(local_path, remote_path):
            file_hashes[local_path] = new_hash
            if POST_SYNC_COMMAND:
                self.ssh.run_command(POST_SYNC_COMMAND)
            
            # Feature 8: Health Check Ping
            if HEALTH_CHECK_URL:
                self.ping_health_check()

    def delete_remote(self, local_path):
        if self.should_ignore(local_path):
            return
        remote_path = self.get_remote_path(local_path)
        self.ssh.remove_file(remote_path)
        if local_path in file_hashes:
            del file_hashes[local_path]

    def ping_health_check(self):
        try:
            logger.info(f"Performing health check: {HEALTH_CHECK_URL}")
            with urllib.request.urlopen(HEALTH_CHECK_URL, timeout=10) as response:
                if response.getcode() == 200:
                    logger.info("Health check passed!")
                else:
                    logger.warning(f"Health check returned code: {response.getcode()}")
        except Exception as e:
            logger.error(f"Health check failed: {e}")

def full_sync(ssh_manager):
    logger.info("Performing full sync...")
    count = 0
    local_files = []
    
    for root, dirs, files in os.walk(LOCAL_DIR):
        dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(os.path.join(root, d).replace("\\", "/"), f"*{ignore}*") for ignore in IGNORE_LIST)]
        
        for file in files:
            local_path = os.path.join(root, file)
            if any(fnmatch.fnmatch(local_path.replace("\\", "/"), f"*{ignore}*") for ignore in IGNORE_LIST):
                continue
            
            local_files.append(local_path)
            relative_path = os.path.relpath(local_path, LOCAL_DIR)
            remote_path = os.path.join(REMOTE_DIR, relative_path).replace("\\", "/")
            
            new_hash = get_file_hash(local_path)
            if ssh_manager.upload_file(local_path, remote_path):
                file_hashes[local_path] = new_hash
                count += 1
    
    # Feature 9: Remote Mirroring (Cleanup)
    if MIRROR_REMOTE and not args.dry_run:
        # This is complex to implement perfectly with SFTP, requires recursive listing
        logger.info("Remote mirroring enabled (Cleanup not fully implemented in SFTP mode yet)")

    logger.info(f"Full sync completed. {count} files uploaded.")
    if count > 0 and POST_SYNC_COMMAND:
        ssh_manager.run_command(POST_SYNC_COMMAND)

if __name__ == "__main__":
    print("-" * 50)
    print(f"      DIRECT-DEPLOY (Profile: {args.profile})")
    if args.dry_run: print("      *** DRY RUN MODE ENABLED ***")
    print("-" * 50)
    
    if not os.path.exists(LOCAL_DIR):
        os.makedirs(LOCAL_DIR)

    ssh_manager = SSHManager(config["server"])
    
    if args.full_sync:
        full_sync(ssh_manager)

    event_handler = DeployHandler(ssh_manager)
    observer = Observer()
    observer.schedule(event_handler, LOCAL_DIR, recursive=True)
    
    logger.info(f"Monitoring: {LOCAL_DIR}")
    logger.info(f"Target: {REMOTE_DIR}")
    
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    logger.info("Goodbye!")
