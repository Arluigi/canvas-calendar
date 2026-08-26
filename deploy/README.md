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
