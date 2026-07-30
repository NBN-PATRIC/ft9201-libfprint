# Fingerprint on the KDE lock screen, on Debian/Kali

This is not FT9201-specific — it applies to **any** fprintd-supported reader on
Debian-family systems running KDE Plasma. It is here because anyone getting this
sensor working on Kali will hit it next.

## Why fingerprint never appears on the lock screen

KDE ships its PAM stacks in `/usr/lib/pam.d/` (`kde`, `kde-fingerprint`,
`kde-smartcard`). **Debian does not read PAM stacks from there** — its PAM policy
only honours `/etc/pam.d/`. The journal says so explicitly, on every unlock:

```
PAM (kde-fingerprint) /usr/lib/pam.d is not supported on this system
```

So the greeter asks for fingerprint auth, Debian refuses the service file, and the
conversation falls back to `/etc/pam.d/other` — password. Fingerprint on the KDE
lock screen is not disabled on these systems; it is **unreachable by file path**.

The fix is a `kde-fingerprint` stack in `/etc/pam.d/`. The one here is based on
KDE's own, with changes worth understanding:

## 1. `timeout=-1 max-tries=-1` on `pam_fprintd`

The greeter arms the fingerprint conversation **once, when the screen locks**.
With the default 30 s timeout, locking manually and touching immediately works,
but an idle lock means the conversation is long dead by the time you return —
fingerprint silently unavailable, and the greeter's method label degenerates into
an orphaned *"or smartcard"*. Negative values keep verification active for as
long as the screen is locked (the greeter dies on unlock, cancelling the
conversation — the same design gdm-fingerprint relies on). This is what makes it
behave like Windows Hello: touch at any moment, screen wakes and unlocks.

## 2. `pam_kwallet5` removed from the fingerprint path

It cannot work there: `pam_kwallet5` derives the wallet key from `PAM_AUTHTOK`,
which only `pam_unix` fills. On a fingerprint authentication there is no
cleartext password, and the module just logs *"Couldn't get password (it is
empty)"*. Keeping the line opens nothing.

## 3. The wallet guard (`kwallet-aberta`) — for autologin setups

Consequence of (2): if your machine autologins, the wallet starts **closed** and
only a typed password ever opens it. If the *first* unlock after boot happens by
fingerprint, the wallet stays closed and everything hanging on the Secret Service
(Chromium/Electron apps) degrades or blocks.

The guard is a `pam_exec requisite` that only lets the fingerprint stack proceed
if the wallet is already open. Measured on the reference machine: locking the
screen does **not** close the wallet (118 locks across a day, same `kwalletd6`
PID), so the guard bites exactly once per boot — first unlock is by password,
every one after that is a touch. It fails **closed**: any error (no session, no
D-Bus, unknown user) refuses fingerprint and password takes over.

If your machine does not autologin, `pam_kwallet5` in the *password* path already
opened the wallet at login and you can drop the guard line.

## 4. `kde-smartcard` explicitly denied

Without a file in `/etc/pam.d/`, that service also falls back to `other` — so the
greeter believes a "smartcard" method exists (backed by a password prompt!), and
its label leaks into the UI. One `pam_deny` line ends the conversation instantly.

## Installing

```bash
sudo install -m0755 kwallet-aberta   /usr/local/sbin/kwallet-aberta
sudo install -m0644 kde-fingerprint  /etc/pam.d/kde-fingerprint
sudo install -m0644 kde-smartcard    /etc/pam.d/kde-smartcard
```

Safety properties, by construction: the password path is the `kde` service, which
keeps falling through to `/etc/pam.d/other` and never touches these files. Worst
case of any mistake here is fingerprint not being offered. Rollback is deleting
the two files. Test with a way back in (SSH, a TTY) before trusting it, as with
any PAM change.

## 5. Waking the display on fingerprint unlock (X11)

One more thing Windows drivers do for you: the reader is **not an input device**.
Mouse and keyboard wake the display because they generate input events; a PAM
unlock generates none, so unlocking by finger with the display in DPMS off leaves
the session unlocked *in the dark* — measured exactly that way here.

`wake-screen-on-unlock` fixes it: kscreenlocker announces lock state on the
session bus (`org.freedesktop.ScreenSaver.ActiveChanged`), and on unlock the
watcher forces DPMS on. Install as a systemd user unit:

```bash
install -m0755 wake-screen-on-unlock ~/.local/bin/
install -m0644 wake-screen-on-unlock.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wake-screen-on-unlock.service
```

X11 only (`xset`); on Wayland KWin handles the wake itself and the watcher is
harmlessly useless.

## 6. Gating Secret Service consumers at autostart

Consequence of §3 on an autologin machine: the wallet is closed until the first
password unlock, so anything autostarted before that sees no Secret Service.
Electron/Chromium apps check `isEncryptionAvailable` **once, at startup** — start
one too early and it silently falls back to plaintext storage for the rest of its
run, and its session does not persist.

`start-after-secretservice.sh` wraps such an app in its `.desktop` Exec line and
waits for `org.kde.KWallet.isOpen` before `exec`-ing it.

It waits **indefinitely** by default, and that default matters. An earlier version
gave up after 300 s and launched anyway, which defeats the purpose in the most
likely case: power on the machine and walk away. Come back half an hour later and
the app has long been running degraded, with nothing on screen to say so. Waiting
forever is strictly better — the app simply does not run, a notification explains
why, and unlocking the wallet starts it. `SECRETGATE_TIMEOUT=<seconds>` restores
the old give-up behaviour if you want it.

Note that apps launched by hand need no gate: by definition you launched them
after unlocking. And Chromium started with `--password-store=basic` does not use
the wallet at all.

## 7. Do not D-Bus-activate the wallet daemon — this one bites hard

A `gdbus call --dest org.kde.kwalletd6 …` **starts kwalletd6** if it is not
already running. That is a side effect, and on an autologin machine it is a
destructive one.

`pam_kwallet5` hands the wallet key to kwalletd over a socket it creates when it
starts the daemon itself. If something else has already brought kwalletd up over
D-Bus, that socket does not exist, so the key has nowhere to go: `pam_kwallet5`
runs `pam_sm_authenticate` and `pam_sm_setcred` perfectly happily, logs nothing
alarming, and **the wallet still never opens**.

Both the gate and the fingerprint guard query the wallet, so both were doing this
at boot — before the first password unlock. Observed failure, and note how far the
symptoms land from the cause:

- wallet never opens, despite the user typing the correct password
- every Secret Service consumer sits in the gate forever (with §6's indefinite
  wait, *forever* is literal — measured at 7370 s before manual intervention)
- the fingerprint guard sees a closed wallet and refuses fingerprint, so lock
  screen unlock silently reverts to password-only
- kwallet's own prompt keeps reappearing

One stray `gdbus` call, four unrelated-looking symptoms.

The fix is to ask `org.freedesktop.DBus.NameHasOwner` first, which answers
**without activating anything**:

```bash
[ "$(gdbus call --session --dest org.freedesktop.DBus \
      --object-path /org/freedesktop/DBus \
      --method org.freedesktop.DBus.NameHasOwner org.kde.kwalletd6)" = "(true,)" ] \
  || exit 1     # daemon not up on its own => wallet not open, and leave it alone
```

If the daemon is not running, the wallet is definitionally not open, so refusing
costs nothing. Only query `isOpen` once someone else has legitimately started it.

Under PAM the guard runs as root, so **both** calls must be made in the target
user's bus context (`runuser` + `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus`).
Checking `NameHasOwner` on root's own bus always says "no" and silently refuses
every fingerprint unlock.
