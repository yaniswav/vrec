# Troubleshooting

Every message vrec can show you, what causes it, and how to fix it.

## Startup and connections

### "Chrome not found: run launch_chrome.bat first"

Chrome must be opened with `scripts\windows\launch_chrome.bat`, not with its normal desktop icon or
taskbar shortcut. That script opens Chrome with the DevTools port vrec needs to control it.

### "Can't reach OBS: open OBS and enable the WebSocket server (Tools > WebSocket Server Settings)."

OBS isn't running, or its WebSocket server isn't enabled. Open OBS, then **Tools > WebSocket Server
Settings** and check **Enable WebSocket server**.

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
