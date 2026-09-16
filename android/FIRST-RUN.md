# Getting it onto the Z Fold 8

Written for someone who has not used Android Studio before. Roughly an hour the
first time, most of it downloads.

Two honest warnings before you start:

- **The first build will probably fail.** I wrote this without a Kotlin compiler
  or an Android SDK — the container I work in has neither, and Google's, Gradle's
  and Maven's servers are all blocked from it. The interval tables and the
  synthesis constants are verified by test; the Kotlin itself has never been
  compiled. Expect ordinary compile errors. Paste them to me and I will fix them.
- **You need about 10 GB free** on the computer for Android Studio and the SDK.

---

## 1. Android Studio

Download from <https://developer.android.com/studio>, install, and open it.

On first launch it runs a setup wizard. **Accept the defaults** — it downloads
the SDK, the platform tools and an emulator image. This is the long part.

## 2. Open the project

Get the code onto the computer:

    git clone https://github.com/naikamit/KiteCast.git

This makes a `KiteCast` folder wherever you ran it.

In Android Studio: **Open**, then select the `android` folder inside `KiteCast`
— *not* the `KiteCast` folder itself. It must be the folder containing
`settings.gradle.kts`.

It will say "Gradle sync in progress" and take a few minutes. If it offers to
upgrade the Android Gradle Plugin, **say no for now** — the versions in the
project are deliberately conservative.

## 3. The speech model

This is the one thing that is not in the repository, because it is a 40 MB
third-party binary.

**Windows** (cmd — `tar` is built in since Windows 10; `unzip` is not):

    cd KiteCast\android\app\src\main\assets
    curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    tar -xf vosk-model-small-en-us-0.15.zip
    ren vosk-model-small-en-us-0.15 model-en-us
    echo vosk-model-small-en-us-0.15> model-en-us\uuid
    del vosk-model-small-en-us-0.15.zip
    dir model-en-us

**macOS / Linux**:

    cd KiteCast/android/app/src/main/assets
    curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip
    mv vosk-model-small-en-us-0.15 model-en-us
    echo vosk-model-small-en-us-0.15 > model-en-us/uuid
    rm vosk-model-small-en-us-0.15.zip
    ls model-en-us

You should end up with `assets/model-en-us/` containing `am`, `conf`, `graph`
and `ivector` folders. If that path is wrong the app builds and runs but hears
nothing, and the log says the model failed to unpack.

## 4. Put the phone in developer mode

On the Z Fold 8:

1. **Settings → About phone → Software information**
2. Tap **Build number** seven times. It counts down, then says you are a
   developer. It will ask for your PIN.
3. Back out to **Settings → Developer options** (near the bottom).
4. Turn on **USB debugging**.

While you are there, also turn on **Stay awake** if you want the screen to stay
on while plugged in. Optional but convenient.

## 5. Plug it in

Use a **USB-C data cable**. This matters: many USB-C cables are charge-only and
the phone will simply never appear. If nothing shows up, try a different cable
before anything else.

On the phone a dialog appears: **Allow USB debugging?** — tick *Always allow
from this computer* and accept.

You may also need to change the USB mode. Pull down the notification shade, tap
the **Charging this device via USB** notification, and choose **File transfer /
Android Auto**. Charge-only mode blocks debugging on some builds.

In Android Studio, the device dropdown at the top should now show something like
**SM-F9xx**. If it says *No devices*, click the dropdown → **Troubleshoot Device
Connections**.

## 6. Run it

Press the green ▶ button, or **Shift + F10**.

First run takes a few minutes. It compiles, installs, and launches.

**If the build fails:** the **Build** tab at the bottom lists the errors. Copy
the whole panel — the file, line and message — and send it to me. That is the
expected outcome of a first build written without a compiler, not a disaster.

## 7. On the phone

Tap **Start**. It asks for:

- **Microphone** — required. Choose *While using the app*; the foreground
  service extends that legitimately.
- **Notifications** — required, because a foreground service must show one.
  That notification is also your pause/skip/stop controls.

Put your wired headphones in the USB-C DAC and answer out loud.

## 8. The part you actually wanted: screen off

Start a session, then press the power button to lock the phone.

It should keep going — playing intervals and listening for your answers, with
the notification on the lock screen showing Pause, Skip and Stop.

**If it dies after a minute, it is Samsung's battery management, not the app.**
One UI is aggressive about this. Fix it:

- **Settings → Apps → Ear → Battery → Unrestricted**
- **Settings → Battery → Background usage limits** → make sure Ear is not in
  *Sleeping apps* or *Deep sleeping apps*
- **Settings → Device care → Memory** → check Ear is not in the auto-cleaned list

## 9. Sending me a log

Long-press the log panel at the bottom of the screen — it copies to the
clipboard. Paste it to me.

What I want to see: the gaps. `mic` lines showing when it opens and closes,
`hear` lines with the `+NNNms` latency. On the web version those were seconds;
offline they should be a fraction of that, and that difference is the whole
point of this build.

---

## Things that commonly go wrong

| symptom | cause |
|---|---|
| `'unzip' is not recognized` | Windows — use `tar -xf` instead |
| phone never appears in Android Studio | charge-only cable, or USB mode not set to file transfer |
| `SDK location not found` | open the `android` folder, not `KiteCast` |
| build fails on `vosk-android` | version bump needed; send me the error |
| app runs, hears nothing | model folder in the wrong place, or the `uuid` file is missing — check step 3 |
| works, then stops when locked | Samsung battery optimisation — step 8 |
| no sound | check the USB-C DAC is seated; the app plays as media, so media volume |
