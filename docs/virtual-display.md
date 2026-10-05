# Virtual display options

vrec places its dedicated Chrome window on a second display so it can run fullscreen without taking
over the screen you're using. You have a few ways to get one.

## Option 1: Virtual Display Driver (recommended)

An open-source driver that adds a display with no physical monitor attached.

1. Install it:

   ```
   winget install --id=VirtualDrivers.Virtual-Display-Driver -e
   ```

2. Open its control app ("VDD Control") and install/enable a virtual display from there.
3. Configure it in Windows display settings (see [Display settings](#display-settings) below).

## Option 2: HDMI/DisplayPort dummy plug

A small headless display emulator (an "HDMI dummy plug" or "DisplayPort EDID emulator") that you plug
into a spare video output. Windows sees it as a real, always-connected monitor. This works well if
your GPU has a free output and you don't want to install a driver.

## Option 3: A real second monitor

Any spare monitor connected to the PC. Simplest option if you have one lying around, though it does
occupy physical space and stays visibly on.

## Option 4: No second screen

vrec also works with a single display: with `[display] screen = "auto"` (the default), vrec uses your
only screen, so no setting is needed. The recording window then occupies that screen while it runs, so
you can't use the PC for anything else at the same time. Fine for short unattended runs overnight.

## Display settings

Whichever option you use, set it up the same way in **Settings > System > Display**:

- Virtual/second display: **Extend desktop to this display**, resolution **3840 x 2160**, refresh rate
  **60 Hz**.
- Your real screen: keep it as **Main display**.

## Turning the virtual display off when not recording

If you'd rather not have an extra display listed all the time, you can disable it between sessions.

**Using the driver's control app** (Virtual Display Driver): open "VDD Control" and turn the virtual
display off/on from there. This is the simplest method if you used option 1.

**Using Device Manager**: **Device Manager > Display adapters**, right-click the virtual display
device, **Disable device** / **Enable device**.

**Using an elevated PowerShell command**: first check the exact device name with:

```powershell
Get-PnpDevice -Class Display
```

Find the virtual display's friendly name in the output, then (in an **elevated** PowerShell prompt,
i.e. "Run as administrator"):

```powershell
Get-PnpDevice -Class Display -FriendlyName '*Virtual*' | Disable-PnpDevice -Confirm:$false
```

and to turn it back on:

```powershell
Get-PnpDevice -Class Display -FriendlyName '*Virtual*' | Enable-PnpDevice -Confirm:$false
```

Adjust the `-FriendlyName` filter to match what `Get-PnpDevice -Class Display` actually showed you:
it may not contain the word "Virtual" depending on the driver.

## Choosing the right screen

By default, `[display] screen = "auto"` in `config.toml` picks the largest screen that isn't your main
one, or your main screen when it is the only one. If you also have a **real second monitor** connected, "auto" can pick that monitor instead of the
virtual display whenever the virtual display happens to be off (for example, right before
`manage_virtual_display` turns it on). vrec doesn't guess which one you meant from the screen list
alone. If you have both:

- set `[display] screen` in `config.toml` to a 1-based screen index (e.g. `"2"`) or part of the
  virtual display's name (e.g. `"DISPLAY3"`), instead of leaving it on `"auto"`;
- run `vrec --doctor` to see which screen the "Virtual screen" check would actually use.

## Turning the virtual display on and off automatically

If you'd rather not remember to turn the virtual display on and off by hand, vrec can do it for you
around each batch, through the `manage_virtual_display` feature (off by default, see
[Features on/off](../README.md#features-onoff) in the README).

1. Open a terminal **as administrator** and run, once:

   ```
   vrec --install-display-helper
   ```

   This matches display adapters whose name contains "Virtual" by default; pass a different pattern if
   yours is named differently, for example:

   ```
   vrec --install-display-helper "*Virtual Display Driver*"
   ```

   The pattern may only contain letters, digits, spaces and these characters: `* ? . ( ) - [ ]`.
   Anything else is refused.

   This writes a small PowerShell script to `%ProgramData%\vrec` and registers two on-demand Windows
   Task Scheduler tasks (under `\vrec\`) that run it with the highest privileges to enable/disable the
   matching adapter(s). This is the only step that needs administrator rights. vrec triggers those
   tasks afterwards (`schtasks /Run`) with no elevation prompt.

   Because the tasks run elevated, the script must not be editable by a standard user. vrec locks the
   `%ProgramData%\vrec` folder so that only Administrators and SYSTEM can write to it; other users can
   read it. Earlier betas kept the script in `%LOCALAPPDATA%\vrec`, where any process of yours could
   change it: installing or uninstalling the helper now deletes those old copies.

2. Turn the feature on:

   ```
   vrec --enable manage_virtual_display
   ```

vrec then turns the virtual display on before a batch (only if it was off) and off again afterwards,
only if this run was the one that turned it on; if you had already turned it on yourself, vrec leaves
it as it found it. It tells the virtual display apart from a real second monitor by asking Windows for
that adapter's own status, not by guessing from the screen list (see
[Choosing the right screen](#choosing-the-right-screen) above).

Once the helper is installed you can also switch the virtual display by hand, with no administrator
prompt:

```
vrec --display status
vrec --display on
vrec --display off
```

`status` shows whether the helper is installed, which adapter(s) it switches, whether the virtual
display is on, and the screens Windows has right now. `on` waits for the new screen to appear and
reports it. `off` is refused while a vrec batch is recording. The same commands are available as
`display_status.bat`, `display_on.bat` and `display_off.bat` (double-click them).

### Off while some programs run

Some games' anti-cheat refuse to start, or kick players, while a virtual display exists. Put their
process names in `config.toml` (Task Manager, Details tab):

```toml
[display]
off_while_running = ["VALORANT-Win64-Shipping.exe"]
```

Then run `vrec --display auto` (or double-click `display_auto.bat`) and leave the window open. It checks
the running programs every 5 seconds, turns the display off when a listed program starts and back on
when none runs anymore, printing one line per change. It never turns the display off while a vrec batch
is recording. Ctrl+C stops it and leaves the display on, unless a listed program is still running. If the
list is empty it says so and exits.

A batch started while a listed program is running prints a warning and goes on.

To remove the scheduled tasks and the helper script (also the old `%LOCALAPPDATA%\vrec` copies):

```
vrec --uninstall-display-helper
```

Both commands only work on Windows.

## GPU driver updates

Before updating your NVIDIA (or other GPU) drivers, uninstall or disable the virtual display first,
then do the update, then reinstall/re-enable it. Updating drivers with a virtual display active can
occasionally leave you with a black screen at startup.

If that happens: press **Win + P**, then press the down arrow and Enter a few times to cycle through
the display modes until your real screen comes back.

## Watching the virtual screen live

In OBS, right-click the Display Capture source and choose **Windowed Projector (Source)**. This opens
a window showing exactly what the virtual display is showing, in real time, without affecting the
recording.
