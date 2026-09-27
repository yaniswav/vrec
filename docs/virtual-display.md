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

vrec also works with a single display: the recording window will simply occupy your main screen while
it runs, so you can't use the PC for anything else at the same time. Fine for short unattended runs
overnight.

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

Adjust the `-FriendlyName` filter to match what `Get-PnpDevice -Class Display` actually showed you —
it may not contain the word "Virtual" depending on the driver.

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
