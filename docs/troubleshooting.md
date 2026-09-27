# Troubleshooting

Every message vrec can show you, what causes it, and how to fix it.

Run `vrec --doctor` first — it checks OBS, Chrome, disk space, VB-CABLE, audio routing and the virtual
screen without recording anything, and often points straight at the fix. Include its output, and the
relevant excerpt from the log file in `data\logs`, in any bug report.

## `vrec --doctor`

Each check prints `[ OK ]`, `[WARN]`, `[FAIL]`, or `[INFO]`, an optional detail, and (for anything that
isn't `[ OK ]`) a `->` hint on how to fix it. It exits with a non-zero code if any check failed. It
never starts a recording or changes OBS/Chrome state (beyond, like a normal run, possibly saving the
OBS WebSocket password the first time it's typed in), so it's safe to run at any time, including while
a normal run is waiting at its "press Enter to start" prompt.

## Pre-flight check

With the `preflight_check` feature on (the default), vrec runs an end-to-end check right before the
first video of every batch, and in `--test` mode: it prints "Checking everything before recording...",
opens a small local test page (served from `http://127.0.0.1`) in the recording Chrome, and runs each
check in turn, printing one line per check: `[ OK ]`, `[FAIL]`, or `[SKIP]` (not applicable here), a
detail, and, for a failing check, a `->` hint.

### Virtual screen

- `[FAIL] Virtual screen: not found`
  -> Turn the virtual display on, or set [display] screen in config.toml.

  No screen matches `[display] screen` (`auto` by default). See
  [virtual-display.md](virtual-display.md).
- `[ OK ] Virtual screen: <screen>` — the screen vrec will record from.

### Chrome window

- `[SKIP] Chrome window: no virtual screen to compare with` — the "Virtual screen" check above already
  failed, so there's nothing to compare the window's position against.
- `[FAIL] Chrome window: not on <screen>`
  -> Move it there with Win+Shift+Arrow, or check [display] screen in config.toml.
- `[FAIL] Chrome window: on <screen> but not fullscreen (<state>)` — the window is on the right screen
  but isn't fullscreen yet; a normal run or `--test` places and fullscreens it itself right after this
  check, so seeing this on a stale window usually isn't a real problem.
- `[ OK ] Chrome window: on <screen>, fullscreen`

### OBS sees Chrome

The test page fills the screen with two solid, unusual colors in turn, and OBS's capture of that
screen must show each one.

- `[ OK ] OBS sees Chrome: '<capture>' shows the Chrome window`
- `[ OK ] OBS sees Chrome: '<capture>' now films <monitor>` — vrec's own display capture (feature
  `obs_scene`) was pointed at the wrong screen; vrec tried the other screens OBS offers for that
  capture and switched to the one that actually shows Chrome. Nothing to do.
- `[FAIL] OBS sees Chrome: '<capture>' doesn't show the Chrome window`
  -> In OBS, point the display capture at the virtual screen, and keep Chrome in front on it.

  With `obs_scene` off, vrec can't retarget your scene's capture itself — point it at the virtual
  screen by hand (see [setup-windows.md](setup-windows.md#4-obs)).

### Sound reaches OBS

The page plays a 1-second, 440 Hz tone — silent to you if it went through VB-CABLE, since that's routed
to OBS rather than played out loud — and OBS's audio meter must move.

- `[SKIP] Sound reaches OBS: audio check unavailable` — the audio meter itself isn't available (see
  "(Audio check unavailable, continuing without it.)" above); there's nothing to check against.
- `[ OK ] Sound reaches OBS: tone played on <sink>`
- `[FAIL] Sound reaches OBS: OBS heard nothing`
  -> Check that the OBS audio source uses CABLE Output (vrec --doctor). (shown with `audio_sink` on)
  -> Route Chrome to CABLE Input in the Windows volume mixer (audio_sink is off). (shown with
  `audio_sink` off)

A check that crashes unexpectedly is reported as failed with its own error message instead of stopping
the batch by itself.

### If something isn't right

In the menu, vrec asks:

> Something isn't right. Record anyway? (y/N)

Answering anything but `y` — and running with `--all`/`--only`, where there's no prompt at all — stops
the batch before anything is recorded, with:

> Pre-flight check failed: nothing was recorded. Fix the points above, or turn the check off: vrec
> --disable preflight_check

### Switching to vrec's own scene (feature `obs_scene`)

Printed while vrec creates or reuses its own OBS scene, just before the pre-flight check runs:

- `Created the OBS scene '<scene>' with a display capture '<capture>'.` — first use: the scene didn't
  exist yet, so vrec created it.
- `Added a display capture '<capture>' to the OBS scene '<scene>'.` — the scene already existed (for
  example one you built by hand) but had no display capture, so vrec added just that; anything else
  already in the scene (a crop filter for 360 videos, say) is left untouched.

Neither message is a problem — recording continues normally. Nothing is printed when the scene already
had everything it needed.

After the batch — and after Ctrl+C — vrec switches OBS back to the scene you were on:

- `Couldn't switch OBS back to the scene '<scene>': ...` — switching back failed; check that the scene
  still exists, and switch to it yourself in OBS. The leftover marker file is left in place in this
  case, so vrec retries the switch itself the next time it starts (see below).

If vrec was killed before it could switch back (crash, forced shutdown, power loss), the next run
detects the leftover marker file (`data\obs_restore_scene.txt`) and finishes the job:

- `Switched OBS back to the scene '<scene>' left over from an interrupted run.` — expected recovery,
  not an error; the marker file is then removed.
- `The scene '<scene>' to switch back to after an interrupted run no longer exists.` — you deleted
  or renamed that scene meanwhile: nothing to switch back to. Pick your scene in OBS; the marker file
  is removed.
- `Couldn't switch OBS back to the scene '<scene>': ...` — same warning as above, this time from the
  leftover-file recovery. The marker file is left in place so the switch is retried on the next start.

## Run logs (`data\logs`)

When the `run_logs` feature is on (the default), every run — recording, `--test`, or `--doctor` —
writes a log file to `data\logs\vrec-YYYYMMDD-HHMMSS.log`: a copy of everything printed to the console,
with in-place progress lines collapsed to their last state, every prompt and its answer (a password is
written as `***`, never the real value), and, if the run crashed unexpectedly, the full traceback. The
last 20 log files are kept; older ones are deleted automatically. Attach the relevant excerpt (not the
whole file) to a bug report.

## Startup and connections

### "Chrome not found: run launch_chrome.bat first"

Chrome must be opened with `scripts\windows\launch_chrome.bat`, not with its normal desktop icon or
taskbar shortcut. That script opens Chrome with the DevTools port vrec needs to control it.

### "Can't reach OBS: open OBS and enable the WebSocket server (Tools > WebSocket Server Settings)."

With the `auto_start_obs` feature off, this is the only message you'll see when OBS isn't reachable:
open OBS, then **Tools > WebSocket Server Settings** and check **Enable WebSocket server**. With
`auto_start_obs` on (the default), vrec starts OBS itself instead — see below.

## Starting OBS and Chrome automatically

By default (features `auto_start_obs`/`auto_start_chrome`), vrec starts OBS and the recording Chrome
itself if they aren't already open when a recording, `--test`, or scheduled run begins, and leaves
them open afterwards. `vrec --doctor` never starts anything itself; instead, when one of them isn't
reachable and its feature is on, it reports `[INFO] ... Not open: vrec will start it (...)` so you can
see in advance what will happen.

### "Starting OBS..." / "OBS started."

Printed while vrec starts OBS itself (feature `auto_start_obs`) and once its WebSocket server answers.
Not an error — informational only.

### "OBS is open but its WebSocket server doesn't answer. In OBS: Tools > WebSocket Server Settings > Enable WebSocket server."

OBS is already running (vrec checked with `tasklist`), so vrec won't start a second copy of it — OBS
would just show "OBS is already running" instead of actually starting. Enable the WebSocket server as
the message says, then run vrec again.

### "OBS isn't open and wasn't found. Open it yourself, or set [obs] path in config.toml."

OBS isn't running, and vrec couldn't locate `obs64.exe` (checked `[obs] path`, then the registry, then
the default install location). Open OBS yourself, or set `[obs] path` in `config.toml` to its
`obs64.exe`.

### "OBS started, but its WebSocket server doesn't answer. In OBS: Tools > WebSocket Server Settings > Enable WebSocket server."

vrec started OBS itself but its WebSocket server still didn't answer after `[obs] start_timeout`
seconds (60 by default). Enable it as the message says (**Tools > WebSocket Server Settings > Enable
WebSocket server**), then run vrec again.

### "Starting the recording Chrome..." / "Recording Chrome started. If the site needs it, log in in that window."

Printed while vrec starts the recording Chrome itself (feature `auto_start_chrome`) and once its debug
port answers. Not an error — if you need to log in to a site the first time, do it in that window; it
uses the same profile as `launch_chrome.bat`.

### "Chrome wasn't found. Install it, or set [chrome] path in config.toml."

vrec couldn't locate `chrome.exe` (checked `[chrome] path`, then the standard install locations, then
the registry). Install Google Chrome, or set `[chrome] path` in `config.toml`.

### "Chrome started, but its debugging port doesn't answer (port <n>)."

vrec started the recording Chrome itself but its debug port still didn't answer after `[chrome]
start_timeout` seconds (30 by default). Try `launch_chrome.bat` yourself and check `vrec --doctor`'s
"Chrome debugging port" line.

### "OBS refused the connection: the password is probably wrong. Restart, it will be asked again."

Run vrec again — it will ask for the password again. The password is cached in
`data\obs_password.txt`; deleting that file also forces the question again. You can find the correct
password in OBS under **Tools > WebSocket Server Settings > Show Connect Info**.

### "Another vrec window is already running. Close it first (lock file: ...)."

vrec uses a lock file (`data\vrec.lock`) to make sure only one copy runs at a time against the same
data directory. This message means another vrec process is already using it. If you're sure no other
copy is actually running (for example, after a crash that didn't clean up), delete `data\vrec.lock`
and try again.

### "An OBS recording is already in progress. Stop it, then restart." / "An OBS recording started meanwhile. Stop it, then restart."

vrec refuses to start if OBS already has a recording running, whether that was already the case at
startup or something (you, another tool) started one while vrec was waiting at the "press Enter to
start" prompt. Stop the other recording in OBS, then run vrec again.

### OBS audio settings restored from a previous run

If vrec was killed (crash, forced shutdown, power loss) while a recording was in progress, it may not
have had a chance to restore your OBS audio mute states on exit. It saves those states to
`data\obs_restore.json` as it goes, and the next time it starts, it detects the leftover file, restores
your OBS mutes from it, prints "Restored OBS audio settings left over from an interrupted run.", and
removes the file. This is expected recovery behavior, not an error.

If that leftover file itself can't be read (for example it was left half-written), vrec prints
"Couldn't read the leftover OBS audio settings file: ignoring it." and deletes it instead of trying to
use it; your OBS audio sources are left as they are, so double-check mute states in OBS in that case.

### "Unexpected error: ... Details in data\logs\vrec-....log"

Something crashed that vrec didn't anticipate. With `run_logs` on (the default), the full traceback is
in that log file — attach it to a bug report. With `run_logs` off, the traceback is printed directly to
the console instead.

## Audio

### "VB-CABLE not found"

VB-CABLE isn't installed, or the PC hasn't been restarted since installing it. See
[setup-windows.md](setup-windows.md#2-vb-cable-virtual-audio-cable).

### "Couldn't send the video's sound to CABLE Input (...): using the Windows audio setup."

The `audio_sink` feature (on by default) couldn't set this page's audio output automatically — often
because the site blocks `setSinkId`, or the page has no audio yet at that point. vrec falls back to
whatever the Windows volume mixer has Chrome routed to; see the fallback setup in
[setup-windows.md](setup-windows.md#fallback-routing-chrome-through-the-windows-volume-mixer).
Recording continues either way.

### "No audio detected" / Audio: NONE

With `audio_sink` on (the default), check `vrec --doctor`'s "Video sound" line, and look for a
"Couldn't send the video's sound to ..." warning during that recording (see above). With `audio_sink`
off, play any video in the Chrome window opened by `launch_chrome.bat`, and check in the Windows volume
mixer that its output is set to **CABLE Input**.

## Video / page

### "The video doesn't fill the whole screen"

The site's player is unusual. The recording still happens; this is informational, not a failure. If
you'd like it fixed, report the site so the fill logic can be adapted.

How fullscreen works: vrec puts the Chrome window into fullscreen (like pressing F11) and makes the
video element cover the whole window, hiding the rest of the page (menus, buttons, logos). It doesn't
use the site's own fullscreen button. The window returns to its normal size at the end.

### "No video found on the page" / "The video is not loading"

Usually one of: a bad link, a page that requires being logged in, or a site with an unusual player.

### Black image

First check that the OBS source is actually capturing the virtual display. If only certain videos come
out black, they are DRM-protected and vrec cannot record them — it isn't a bypass tool. Check whether
the site offers an official download or a companion app (for example a headset app) instead.

## Quality

vrec intercepts the list of available qualities before the player starts and lets only the best one
through, so the video plays at maximum quality from the first second ("Quality: ... forced from the
start"). On sites where that isn't possible, it instead clicks the best quality option in the player's
own menu.

A cap can be configured (`[quality] max_height`, default `0` = no cap, always the best): vrec then picks
the best available quality at or below that height instead of the true best. If every variant offered is
above the cap, the smallest one is used instead. If a video stalls while loading, vrec retries it once
at the next lower quality regardless of this setting (see [Buffering](#buffering) below).

### "Unrecognized streaming format: couldn't force the max quality in advance"

The list of available qualities couldn't be intercepted; the first few seconds might be recorded at
low quality. Report this so the site can be supported properly.

### "Couldn't find a quality selector: the site decides on its own (auto)"

The site's player wasn't recognized, so vrec leaves it on "auto" quality. Report this to get the site
supported.

### "Quality lower than expected"

Your connection can't keep up with the forced quality, so the site is sending a smaller image than
requested. Close other downloads and try again. The actual received resolution is shown ("Received
image: ...").

## Buffering

### "Buffering... X s in reserve (recording paused)"

Normal when your connection can't keep up with the target quality: vrec pauses both the video and the
OBS recording as soon as the loaded reserve drops below the configured threshold (`[buffering]
pause_below`, 2 s by default), then resumes both together once the reserve climbs back up to the
configured threshold (`[buffering] resume_at`, 10 s by default) — or once the reserve has simply
stopped growing for 5 seconds while already at least `pause_below + 2` seconds (4 s by default), so it
doesn't wait forever for a perfectly full buffer that some players never offer. The final file therefore
never contains a frozen frame.

### "OBS refuses to pause"

In OBS, **Settings > Output**: the recording quality must not be set to "Same as stream" — that mode
doesn't support pausing.

### "The video isn't advancing (loading?). Recording continues."

The player is stuck for no visible reason; recording continues in case it recovers. If loading actually
stays stalled for too long (`[buffering] max_stall`, 5 minutes by default), the recording is cut, and
vrec immediately prints "Retrying once below Xp..." and retries that same video right away, at the
quality just below the one that stalled. If that retry finishes normally, its file gets the normal name
and is what counts; the incomplete file from the first attempt is left on disk alongside it. If the
retry also fails to finish (for any reason), the video is renamed `INCOMPLETE - <title>` and marked
failed, so it comes back up under "Record everything" for a further, fully manual attempt.

## Batches

### "Lost connection to Chrome/OBS. Batch stopped; the remaining videos are untouched."

Shown after a video errors out and vrec then finds Chrome or OBS no longer responding. The batch stops
right there; nothing else in the queue is touched, so simply fix the connection (restart Chrome via
`launch_chrome.bat`, or OBS) and run vrec again to pick up where it left off.

### "3 errors in a row: batch stopped."

Three videos in a row ended in an error (not just a `CHECK`/`FAILED` result — an actual exception while
processing the page). vrec stops rather than burning through the rest of the list the same way; check
what's going on (a site change, a network issue) before running it again.

### "Couldn't rename the recording, it keeps its OBS name: ..."

vrec renames each freshly recorded file to match the video's title. If that rename fails — usually
because something else (antivirus, an indexer, OBS itself) is still holding the file — the recording is
kept under the name OBS gave it instead of being lost; look for it by date in the OBS recording folder.

## Display & window placement

### "No virtual screen found to move Chrome to: recording on the screen it is on. Check [display] screen in config.toml, or move it by hand (Win+Shift+Arrow)."

`auto_place_window` (on by default) couldn't find a screen to move Chrome to: `[display] screen`
matches none of the screens Windows currently reports. Turn the virtual display on, check
`[display] screen` in `config.toml`, or see [virtual-display.md](virtual-display.md). Recording
continues on whichever screen the window is already on.

### "Couldn't move Chrome to ... Move it by hand (Win+Shift+Arrow)." / "Couldn't move Chrome: ..."

A screen was found, but the window either didn't land on it or the move itself failed. Both are
warnings, not errors — recording continues on whichever screen the window ends up on; move it
yourself with **Win + Shift + Right Arrow** if needed.

### "Couldn't fullscreen Chrome: ..."

Putting the window into fullscreen failed. Recording continues in whatever state the window is in.

### "The virtual display didn't show up within 15 s. Continuing anyway."

`manage_virtual_display` turned the display adapter on, but no new screen appeared in time. The batch
continues, and Chrome placement then falls back to `[display] screen`. If this happens consistently,
check the adapter in Device Manager.

### "This needs administrator rights once: open a terminal with 'Run as administrator' and run vrec --install-display-helper again."

`vrec --install-display-helper` must be run from an elevated terminal once. After that, no elevation
is needed again — vrec triggers the scheduled tasks it created without a prompt.

### "The virtual display helper isn't installed (run vrec --install-display-helper as admin), or it couldn't start."

`manage_virtual_display` is on, but `--install-display-helper` was never run (or its scheduled tasks
were removed, e.g. by `--uninstall-display-helper`). Run it once as administrator, or see
[virtual-display.md](virtual-display.md).

## Feature toggles

### "Unknown feature in features.toml, ignored: ..." / "Feature '...' in features.toml isn't true/false, ignored: ..."

`data\features.toml` has a name vrec doesn't know, or a value that isn't `true`/`false`. That one line
is ignored (the feature keeps its default); everything else in the file still applies.

### "Unknown feature: ..." (from `vrec --enable`/`--disable`)

The name passed to `--enable`/`--disable` isn't a real feature name. Run `vrec --features` to see the
current, valid names.

## Scheduled runs

`vrec --schedule status` prints "No scheduled run." if none is registered, otherwise its next run time
and the result of its last run (`0` means success). Remember: OBS and the recording Chrome window
(`launch_chrome.bat`) must already be open, and the PC awake, at the scheduled time — the scheduled
task runs `vrec --all`, it doesn't open them for you.

## Output files

### `INCOMPLETE - <title>`

Loading stalled for too long even after the automatic retry, the video was removed from the page
mid-recording, or the wall-clock safety cap was hit. The video is marked as failed so it comes back up
under "Record everything".

### `INTERRUPTED - <title>`

The recording was cut short by Ctrl+C or by an unexpected error partway through. Also marked as failed.

### `FAILED black image - <title>`

The image stayed black for too long (60 s by default) and the recording was abandoned — see
[Black image](#black-image) above.

### `<title> (2).mp4`, `(3).mp4`, ...

vrec never overwrites an existing recording; re-recording a video, or retrying it, adds a numbered
suffix.

## Statuses shown in the menu and summary

| Status | Meaning |
|---|---|
| `OK` | Everything went fine. |
| `CHECK: black image?` | The image was never confirmed as non-black. |
| `CHECK: no audio` | No sound was captured during the recording. |
| `CHECK: time limit reached` | The end of the video wasn't detected; recording was cut at duration + 2 minutes. |
| `FAILED: black image (protected video?)` | Recording abandoned; the video is likely DRM-protected. |
| `FAILED: incomplete (loading stalled)` | Stayed stalled for too long, even after the automatic lower-quality retry. |
| `FAILED: incomplete (video removed from page)` | The video disappeared from the page mid-recording. |
| `FAILED: incomplete (took too long)` | The wall-clock safety cap (`[recording] max_wall_factor` x duration + `max_wall_extra`) was hit. |
| `ERROR: ...` | A problem occurred on the page; the message explains what. |

Several `CHECK` reasons can combine on the same result, e.g. `CHECK: black image?, no audio`. `CHECK`
results are not retried automatically (they need a look first); every `FAILED` result above is retried
by "Record everything".
