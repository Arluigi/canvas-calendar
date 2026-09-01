# Deployment

```bash
canvas-calendar install-agents
```

Renders both plists for the current user and loads them. It refuses to run
against a path inside a git checkout — the original install pointed at
`<repo>/.venv/bin/canvas-calendar`, which meant whichever branch happened to
be checked out decided what ran at 07:15.

Runs at 07:15 and 19:15, plus once at load. Two runs a day rather than one
because the Mac is often asleep in the morning; each run reconciles the whole
term rather than a delta, so a duplicate run is a no-op and a missed run
costs nothing.

Check on it:

```bash
canvas-calendar doctor                    # token, term, calendar, agents
launchctl list | grep canvas              # second column is the last exit code
tail ~/.config/canvas-calendar/daily.log
canvas-calendar digest
```

## Legacy labels

The author's original install uses `com.aryan.canvas-calendar` and
`com.aryan.canvas-debrief`. `install-agents` writes the newer
`io.github.canvas-calendar.{sync,debrief}` labels, so running it on that
machine would schedule every job twice. Boot the old ones out first, or leave
them alone:

```bash
launchctl bootout gui/$UID/com.aryan.canvas-calendar
launchctl bootout gui/$UID/com.aryan.canvas-debrief
rm ~/Library/LaunchAgents/com.aryan.canvas-*.plist
```
