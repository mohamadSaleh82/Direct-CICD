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
import re
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
from datetime import datetime
import threading

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
            content = f.read()
            # Support environment variable expansion: ${VAR_NAME}
            def env_replacer(match):
                var_name = match.group(1)
                return os.environ.get(var_name, match.group(0))
            
            content = re.sub(r"\$\{([^}]+)\}", env_replacer, content)
            data = json.loads(content)
            
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
    """Calculate MD5 hash of a file incrementally to save memory."""
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        logger.error(f"Error hashing {filepath}: {e}")
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
            
            # Enable Transport Compression if requested
            if ENABLE_COMPRESSION:
                transport = self.ssh.get_transport()
                transport.use_compression(True)
                
            self.sftp = self.ssh.open_sftp()
            logger.info("Connected successfully (Compression: %s)!" % ENABLE_COMPRESSION)
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
            
            # Use retry logic for stability
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"Uploading: {os.path.basename(local_path)} (Attempt {attempt+1})")
                    self.sftp.put(local_path, remote_path)
                    return True
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    logger.warning(f"Upload failed, retrying... {e}")
                    time.sleep(2)
            
            return False
                
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

    def list_remote_recursive(self, remote_path):
        """Recursively list files on the remote server."""
        if not self.ensure_connection(): return []
        files = []
        try:
            for entry in self.sftp.listdir_attr(remote_path):
                full_path = remote_path + "/" + entry.filename
                if entry.st_mode & 0o040000: # Directory
                    files.extend(self.list_remote_recursive(full_path))
                else: # File
                    files.append(full_path)
        except:
            pass
        return files

class DeployHandler(FileSystemEventHandler):
    def __init__(self, ssh_manager):
        self.ssh = ssh_manager
        self.debounce_seconds = 0.8
        self.pending_files = set()
        self._timer = None
        self._lock = threading.Lock()

    def _trigger_sync(self):
        with self._lock:
            files_to_sync = list(self.pending_files)
            self.pending_files.clear()
            self._timer = None
        
        if files_to_sync:
            logger.info(f"Syncing bundle of {len(files_to_sync)} changes...")
            for local_path in files_to_sync:
                if os.path.exists(local_path):
                    self.sync_file(local_path)
                else:
                    self.delete_remote(local_path)

    def _schedule_sync(self, path):
        with self._lock:
            self.pending_files.add(path)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._trigger_sync)
            self._timer.start()

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule_sync(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._schedule_sync(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._schedule_sync(event.src_path)

    def on_moved(self, event):
        self._schedule_sync(event.src_path) # Delete old
        self._schedule_sync(event.dest_path) # Create new

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
        
        # Feature 7: Pre-sync Local Command
        # Replacement of os.system with subprocess for security
        if PRE_SYNC_COMMAND:
            logger.info(f"Running local pre-sync command: {PRE_SYNC_COMMAND}")
            import subprocess
            try:
                subprocess.run(PRE_SYNC_COMMAND, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"Pre-sync command failed: {e}")
                return # Stop sync if pre-command fails

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
    
    # Feature 9: Remote Mirroring (Actual Cleanup)
    if MIRROR_REMOTE and not args.dry_run:
        logger.info("Performing remote cleanup (Mirroring)...")
        remote_files = ssh_manager.list_remote_recursive(REMOTE_DIR)
        local_relative_files = {os.path.relpath(p, LOCAL_DIR).replace("\\", "/") for p in local_files}
        
        for r_file in remote_files:
            rel_r_file = os.path.relpath(r_file, REMOTE_DIR).replace("\\", "/")
            if rel_r_file not in local_relative_files:
                logger.info(f"Removing orphaned remote file: {rel_r_file}")
                ssh_manager.remove_file(r_file)

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
