# Troubleshooting

Every message vrec can show you, what causes it, and how to fix it.

## Startup and connections

### "Chrome not found: run launch_chrome.bat first"

Chrome must be opened with `scripts\windows\launch_chrome.bat`, not with its normal desktop icon or
taskbar shortcut. That script opens Chrome with the DevTools port vrec needs to control it.

### "Could not reach OBS"

OBS isn't running, or its WebSocket server isn't enabled. Open OBS, then **Tools > WebSocket Server
Settings** and check **Enable WebSocket server**.

### "OBS connection refused: the password is probably wrong"

Run vrec again — it will ask for the password again. The password is cached in
`data\obs_password.txt`; deleting that file also forces the question again. You can find the correct
password in OBS under **Tools > WebSocket Server Settings > Show Connect Info**.

### "Another instance of vrec is already running"

vrec uses a lock file (`data\vrec.lock`) to make sure only one copy runs at a time against the same
data directory. This message means another vrec process is already using it. If you're sure no other
copy is actually running (for example, after a crash that didn't clean up), delete `data\vrec.lock`
and try again.

### OBS audio settings restored from a previous run

If vrec was killed (crash, forced shutdown, power loss) while a recording was in progress, it may not
have had a chance to restore your OBS audio mute states on exit. It saves those states to
`data\obs_restore.json` as it goes, and the next time it starts, it detects the leftover file, restores
your OBS mutes from it, and removes the file. This is expected recovery behavior, not an error.

## Audio

### "VB-CABLE not found"

VB-CABLE isn't installed, or the PC hasn't been restarted since installing it. See
[setup-windows.md](setup-windows.md#2-vb-cable-virtual-audio-cable).

### "No audio detected" / Audio: NONE

Play any video in the Chrome window opened by `launch_chrome.bat`, and check in the Windows volume
mixer that its output is set to **CABLE Input**.

## Video / page

### "The video doesn't fill the whole screen"

The site's player is unusual. The recording still happens; this is informational, not a failure. If
you'd like it fixed, report the site so the fill logic can be adapted.

How fullscreen works: vrec puts the Chrome window into fullscreen (like pressing F11) and makes the
video element cover the whole window, hiding the rest of the page (menus, buttons, logos). It doesn't
use the site's own fullscreen button. The window returns to its normal size at the end.

### "No video found on the page" / "The video won't load"

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

A cap can be configured (`[quality] max_height`, default 2880): vrec then picks the best available
quality at or below that height instead of the true best.

### "Streaming manifest not recognized"

The list of available qualities couldn't be intercepted; the first few seconds might be recorded at
low quality. Report this so the site can be supported properly.

### "Quality selector not found"

The site's player wasn't recognized, so vrec leaves it on "auto" quality. Report this to get the site
supported.

### "Quality lower than expected"

Your connection can't keep up with the forced quality, so the site is sending a smaller image than
requested. Close other downloads and try again. The actual received resolution is shown ("Received
image: ...").

## Buffering

### "Loading... (recording paused)"

Normal when your connection can't keep up with the target quality: vrec pauses both the video and the
OBS recording as soon as the loaded buffer drops below the configured threshold (2 s by default), waits
until enough is buffered again (10 s by default), and then resumes both together. The final file
therefore never contains a frozen frame. If you interrupt a batch, choosing "Record everything" again
later picks up where it stopped.

vrec also resumes automatically once the buffer stops growing for 5 seconds while already above the
resume threshold plus 2 seconds — it doesn't wait forever for a perfectly full buffer.

### "OBS refuses to pause"

In OBS, **Settings > Output**: the recording quality must not be set to "Same as stream" — that mode
doesn't support pausing.

### "The video isn't advancing (stuck loading?)"

The player is stuck for no visible reason. Check the recorded file; if loading stays stalled for too
long (5 minutes by default), the recording is stopped and the file is renamed
`INCOMPLETE - <title>` (see below). vrec then automatically retries once, one quality step lower, the
next time you record that video.

## Output files

### `INCOMPLETE - <title>`

Loading stalled for too long, or the video was removed from the page mid-recording. The video is
marked as failed so it comes back up under "Record everything".

### `INTERRUPTED - <title>`

The recording was cut short by Ctrl+C or by an unexpected error partway through. Also marked as failed.

### `FAILED black image - <title>`

The image stayed black for too long (60 s by default) and the recording was abandoned — see
[Black image](#black-image) above.

### `<title> (2).mp4`, `(3).mp4`, ...

vrec never overwrites an existing recording; re-recording a video adds a numbered suffix.

## Statuses shown in the menu and summary

| Status | Meaning |
|---|---|
| `OK` | Everything went fine. |
| `CHECK: loading stalled` | The video failed to load for 5 minutes; recording was stopped. |
| `CHECK: no audio` | No sound was captured during the recording. |
| `CHECK: black image?` | The image was never confirmed as non-black. |
| `CHECK: time limit reached` | The end of the video wasn't detected; recording was cut at duration + 2 minutes. |
| `FAILED: black image (protected video?)` | Recording abandoned; the video is likely DRM-protected. |
| `ERROR: ...` | A problem occurred on the page; the message explains what. |

`CHECK` results are not retried automatically (they need a look first); `FAILED` results are retried by
"Record everything".
