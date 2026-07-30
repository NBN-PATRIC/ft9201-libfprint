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
