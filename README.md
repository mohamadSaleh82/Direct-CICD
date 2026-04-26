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

## Features | ویژگی‌ها
- **Live Sync:** Instant upload upon file save. (همگام‌سازی آنی به محض ذخیره فایل)
- **Auto Reconnect:** Resilient to connection drops. (اتصال خودکار در صورت قطع اینترنت)
- **Logging:** Detailed logs saved to `deploy.log`. (ثبت گزارشات در فایل لاگ)
- **Post-Sync Command:** Execute commands on server after upload (e.g., restart service). (اجرای دستورات روی سرور پس از آپلود)
- **Debounced Sync:** Prevents corrupt uploads during rapid changes. (جلوگیری از آپلود فایل‌های ناقص)

## Usage | نحوه استفاده

1. **Install Requirements | نصب پیش‌نیازها:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration | تنظیمات:**
   Edit `config.json` with your server details and paths.
   فایل `config.json` را با اطلاعات سرور و مسیرهای خود ویرایش کنید.

3. **Run | اجرا:**
   ```bash
   python direct_deploy.py
   ```

## Configuration (config.json)
- `server`: Host, Username, Password/Key details.
- `paths`: `local_path` (your PC) and `remote_path` (server).
- `ignore`: List of files/folders to exclude (e.g. `.git`, `node_modules`).
- `post_sync_command`: A command to run on the server after each sync.

---
Developed for **opan.ir** project.
توسعه یافته برای پروژه **opan.ir**
