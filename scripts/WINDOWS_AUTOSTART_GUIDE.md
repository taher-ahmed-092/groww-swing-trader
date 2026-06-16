# Windows auto-start (WSL) — run Cosmic Punk at logon

Your system runs inside WSL. This makes Windows start it automatically every time
you log in, so the trader is always up without opening a terminal.

There are two ways. The Scheduled Task is the robust one; the Startup folder is the
quick one.

---

## Prerequisite (do this once, inside WSL)

```bash
cd ~/groww-swing-trader
bash scripts/autostart_setup.sh
```

This creates `~/.cosmic_punk_startup.sh` (waits for network, then launches
`scripts/start_everything.py`). Both methods below just invoke that script.

---

## Option A — Scheduled Task (recommended, auto-restarts)

In **PowerShell** (on Windows, not inside WSL):

```powershell
cd \\wsl$\Ubuntu\home\<you>\groww-swing-trader   # or your repo path
powershell -ExecutionPolicy Bypass -File scripts\windows_task_scheduler_setup.ps1
```

If your distro isn't named `Ubuntu`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows_task_scheduler_setup.ps1 -WslDistro Ubuntu-22.04
```

(Find your distro name with `wsl -l -v`.) The task triggers at logon and retries up
to 3× if it fails. Remove it later with:

```powershell
Unregister-ScheduledTask -TaskName CosmicPunkTrader -Confirm:$false
```

## Option B — Startup folder (simplest)

1. Press **Win+R**, type `shell:startup`, press Enter.
2. Create `cosmic_punk.bat` in that folder containing:
   ```bat
   @echo off
   wsl -d Ubuntu -e bash -c "bash ~/.cosmic_punk_startup.sh"
   ```
   (Replace `Ubuntu` with your distro name if different.)

That's it — it launches at every logon.

---

## Verify

After logging out and back in (or rebooting):
- Your Telegram bot should message you within ~30 seconds with the dashboard link.
- If not: open WSL and run `uv run python scripts/verify_setup.py`, and check
  `~/groww-swing-trader/logs/startup.log`.

## Notes
- WSL must be allowed to keep running in the background. On Windows 11, WSL stays
  alive while you're logged in; if you want it to survive logout, run a 24/7 host
  (Termux on a phone, or Oracle Cloud — see the other guides) instead.
- Editing `.env`? Restart: `wsl -d Ubuntu -e bash -c "pkill -f start_everything; bash ~/.cosmic_punk_startup.sh"`.
