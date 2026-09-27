# Windows setup

One-time setup for vrec. Do these steps in order; each one is quick, and you won't need to repeat it
unless you reinstall something.

## 1. Virtual display

vrec needs a second display that Chrome can be placed on, invisible to your normal desktop use. See
[virtual-display.md](virtual-display.md) for the available options and detailed instructions.

Once you have one set up, in Windows display settings:

- Virtual display: **Extend desktop to this display**, 3840 x 2160, 60 Hz.
- Your real screen: **Make this my main display**.

## 2. VB-CABLE (virtual audio cable)

VB-CABLE lets OBS capture Chrome's audio without it ever reaching your speakers.

1. Download "VB-CABLE Driver" from [vb-audio.com](https://vb-audio.com/Cable/) and unzip it.
2. Right-click `VBCABLE_Setup_x64.exe` and choose **Run as administrator**, then **Install Driver**.
3. Restart your PC.
4. Play any video in the Chrome window opened by `launch_chrome.bat` (see step 4 below), then open
   **Settings > System > Sound > Volume mixer**, find the Chrome entry, and set its output to
   **CABLE Input**. Windows remembers this per application afterwards.

Side effect: this setting applies to *every* Chrome window, including your everyday browser — it will
be muted too. To get its sound back, go to the volume mixer and set that Chrome entry's output back to
**Default**.

## 3. Python

1. Open a terminal and run:

   ```
   winget install Python.Python.3.12
   ```

2. Run `scripts\windows\install.bat`. It installs vrec and its dependencies with pip.

## 4. OBS

1. **Tools > WebSocket Server Settings**, check **Enable WebSocket server**.
2. In your scene, add a **Display Capture** source pointed at the virtual display, with
   **Capture Cursor** unchecked.
3. **Settings > Video**: base (canvas) resolution **3840x2160**, FPS 60 (or 30).
4. **Settings > Output**: encoder **NVENC HEVC**, recording quality **not** "Same as stream" (see
   below), format **Hybrid MP4** (or MKV if hybrid MP4 isn't available).

   Recording quality must not be "Same as stream" because vrec needs to be able to pause the OBS
   recording independently while a video buffers; that mode does not support pausing.

vrec manages audio automatically: it creates a "Chrome Audio (VB-CABLE)" source, mutes your desktop
audio and microphone while recording, and restores everything afterwards.

### 360-degree videos

For videos whose image is twice as wide as it is tall:

1. **Settings > Video**: set both base and output resolution to **3840x1920**.
2. Right-click the Display Capture source > **Filters > "+" > Crop/Pad**: top **120**, bottom **120**.
3. Right-click the source > **Transform > Fit to screen**.

Without this, the recorded file has black bars at the top and bottom, and the image looks distorted in
a headset.

## 5. Chrome

1. Double-click `scripts\windows\launch_chrome.bat`. It opens a separate Chrome window on a dedicated
   profile (so it doesn't interfere with your everyday browsing).
2. The first time, log in to any site you need to, in that window.
3. Move the window to the virtual display: click it, then press **Win + Shift + Right Arrow** (repeat
   until it lands on the virtual display).
4. Maximize the window. Never minimize it while vrec is running — a minimized window can't be
   captured or controlled correctly.

You're ready to go — see the main [README](../README.md#quick-start) for day-to-day usage.
