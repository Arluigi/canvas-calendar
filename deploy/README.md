# Deployment

```bash
sed "s|{{HOME}}|$HOME|g" deploy/com.aryan.canvas-calendar.plist.template \
  > ~/Library/LaunchAgents/com.aryan.canvas-calendar.plist
launchctl load ~/Library/LaunchAgents/com.aryan.canvas-calendar.plist
```

Runs at 07:15 and 19:15, plus once at load. Two runs a day rather than one
because the Mac is often asleep in the morning; each run reconciles the whole
term rather than a delta, so a duplicate run is a no-op and a missed run
costs nothing.

Check on it:

```bash
launchctl list | grep canvas-calendar     # second column is the last exit code
tail ~/.config/canvas-calendar/daily.log
canvas-calendar digest
```

Exit codes: 0 clean, 1 errors during apply, 2 Canvas token expired,
3 Outlook auth failed. Each also fires a macOS notification.

## Waking the Mac for the 07:00 debrief

launchd runs a *missed* StartCalendarInterval job when the Mac wakes, so a
closed laptop means the debrief arrives whenever you open it — late, not lost.
To actually wake up to it, schedule a hardware wake five minutes earlier:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 06:55:00
pmset -g sched          # verify
```

Caveats, both deliberate macOS behaviour rather than misconfiguration:

- **On AC power this works, lid open or closed.**
- **On battery with the lid closed it does not.** macOS ignores scheduled
  wakes to preserve battery. The only override is `pmset disablesleep 1`,
  which keeps the machine awake all night and drains it — worse than a late
  email.
- A Mac that is fully **powered off** never runs the missed job at all.

The Mac wakes, runs for a few seconds, and returns to sleep on its own. The
debrief is guarded to send once per calendar day, so a scheduled wake plus a
later lid-open cannot produce two emails.

To remove the wake schedule: `sudo pmset repeat cancel`
