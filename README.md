# Direct-Deploy (CI/CD Tool)

این ابزار برای همگام‌سازی لحظه‌ای (Real-time Sync) بین سیستم شما و سرور از طریق SSH طراحی شده است.

> [!IMPORTANT]
> **علت ساخت این ابزار:**
> به دلیل محدودیت‌های اینترنتی در ایران و احتمال قطع شدن دسترسی به سرویس‌های خارجی (مثل GitHub Actions, GitLab CI/CD و ...)، این ابزار به گونه‌ای طراحی شده که بدون نیاز به هیچ سرویس واسطه‌ای و به صورت مستقیم (Direct)، تغییرات کد شما را به سرور منتقل کند. این یعنی حتی در شرایط اینترنت ملی، توسعه و استقرار پروژه شما متوقف نخواهد شد.

---

## English Description

This tool is designed for **Real-time Synchronization** between your local machine and a remote server via SSH.

### Why this tool?
Due to internet restrictions in Iran and the potential loss of access to international CI/CD services (like GitHub Actions, CircleCI, etc.), this tool provides a direct, peer-to-peer deployment mechanism. It ensures that your delivery pipeline remains functional even during global connectivity outages or "National Internet" (Intranet) transitions.

## Advanced Features
1.  **Multi-Profile Support:** Manage multiple servers/projects in one config. 
2.  **Dry Run Mode:** Test your deployment without making changes (`--dry-run`).
3.  **Content Hashing:** Only upload files if their content actually changed.
4.  **SSH Key Auth:** Support for private key authentication. 
5.  **Remote Backup:** Auto-backup of remote files before overwrite. 
6.  **Pre-Sync Commands:** Run local scripts (like build) before sync.
7.  **Health Check Ping:** Auto-verify website status after deployment.
8.  **Glob Pattern Ignore:** Flexible file exclusion using patterns.
9.  **Auto Reconnect:** Resilient to connection drops.
10. **Logging:** Detailed history saved to `deploy.log`.

## Usage 

1. **Install Requirements**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration**
   Edit `config.json` with your server details and profiles.


3. **Run**
   ```bash
   # Standard Run 
   python direct_deploy.py
   
   # With Profile 
   python direct_deploy.py --profile production
   
   # Dry Run 
   python direct_deploy.py --dry-run
   
   # Full Sync on Start
   python direct_deploy.py --full-sync
   ```

## Configuration (config.json)
- `key_path`: Path to your private SSH key (optional).
- `pre_sync_command`: Local command to run before upload.
- `health_check_url`: URL to ping after successful sync.
- `enable_backup`: Set to `true` to keep `.bak` files on server.
- `ignore`: Supports glob patterns (e.g., `**/*.tmp`).

---
Developed for **opan.ir** project.
