# FocalTech FT9201 (`2808:93a9`) — protocol and libfprint driver

Open protocol documentation and a work-in-progress libfprint driver for the **FocalTech FT9201**
USB fingerprint sensor, which has no open driver in any released libfprint version.

Everything here was produced by capturing USB traffic from the vendor driver and re-implementing
it independently. **No proprietary code, blobs or binaries are included or redistributed.**

## Status — read this first

| Capability | State |
|---|---|
| Protocol documented | ✅ [`PROTOCOL.md`](PROTOCOL.md) |
| Reading real images (independent implementation) | ✅ [`reference/ft9201_read.py`](reference/ft9201_read.py) |
| libfprint driver compiles cleanly (master 1.94.100) | ✅ |
| Device recognised by `fprintd`, opens, detects finger | ✅ |
| Image capture through the driver | ✅ |
| Minutiae extraction | ⚠️ 1–3 per frame, no match — see [`MATCHING.md`](MATCHING.md) |
| **Enrollment completes** | ✅ template stored under `/var/lib/fprint/<user>/ft9201/` |
| **Verification matches** | ⚠️ **not yet** — returns `verify-no-match`; tuning needed |

So: **this is not a finished driver, and matching is not a calibration problem.**
Two approaches have been measured on this sensor and both fail: NBIS minutiae (at most 3
per frame, no match in 90 parameter combinations) and correlation over subtemplates
(d′ ≈ 0.2 against impostors captured on this same sensor — genuine and impostor scores are
effectively one distribution). See [`MATCHING.md`](MATCHING.md).

What *is* solid is the capture side.
The protocol section, however, is complete and verified, and should be immediately useful to
anyone stuck on this sensor.

Tested on: Kali Linux (rolling, kernel 6.17), machine with USB `2808:93a9` + `2808:6553`.

## Key finding: vendor request `0x6F`

The request that actually makes reads work on this revision is **`0x6F`**, which does not appear
in any existing open driver:

```
0x40 0x6F  wValue=<LENGTH>  wIndex=<ADDRESS>   -> set up the next bulk read
```

| `wValue` | `wIndex` | Reads |
|---|---|---|
| `0x0020` (32) | `0x9180` | status block |
| `0x1400` (5120) | `0x9080` | **image, 64×80, 8 bpp** |
| `0x0000` | `0xFF00` | mode reset (issued before an image read) |

[`banianitc/ft9201-fingerprint-driver`](https://github.com/banianitc/ft9201-fingerprint-driver)
uses `0x35` for this, which arms nothing here. That is a plausible explanation for the
long-standing *"initialises but never reads"* reports
([#3](https://github.com/banianitc/ft9201-fingerprint-driver/issues/3),
[#10](https://github.com/banianitc/ft9201-fingerprint-driver/issues/10),
[#19](https://github.com/banianitc/ft9201-fingerprint-driver/issues/19)).

Full details, including the capture sequence and finger-presence polling, in
[`PROTOCOL.md`](PROTOCOL.md).

## Three things the driver has to get right

1. **Arm before sensing.** Until a trigger + status read happens, the presence byte is pinned at
   `0x00` forever. Indistinguishable from a dead sensor.
2. **Re-arm about once per second.** Detection goes quiet a few seconds after init — measured
   **1 transition in 4445 polls** without re-arming, **24** with it.
3. **Enlarge before minutiae detection.** At the native 64×80, NBIS `mindtct` returns
   *"No minutiae found"* on every frame, even with clearly defined ridges (σ ≈ 64, full dynamic
   range). `fpi_image_resize (img, 3, 3)` → 192×240 makes enrollment complete. Same approach as
   the `egis0570` / `elanspi` / `aes3k` drivers.

## Layout

```
PROTOCOL.md                     protocol specification (verified)
reference/ft9201_read.py        independent reader — libusb via ctypes, no deps
reference/usbmon-*.txt          USB captures backing the spec
driver/ft9201.c                 libfprint FpImageDevice driver (LGPL-2.1+)
driver/meson-integration.patch  how to add it to the libfprint tree
```

### Quick start — read an image without libfprint

```bash
sudo systemctl stop fprintd        # fprintd holds the device
sudo python3 reference/ft9201_read.py out.png
sudo systemctl start fprintd
```

### Build the driver

See [`driver/meson-integration.patch`](driver/meson-integration.patch). To test without replacing
your system libfprint:

```bash
sudo systemctl stop fprintd
sudo LD_LIBRARY_PATH=<build>/libfprint G_MESSAGES_DEBUG=all /usr/libexec/fprintd
```

## Note on `2808:6553` — it cannot capture

Modules like this one expose two USB functions: `93a9` (the image sensor) and `6553`
(FT9365 "ESS", a secure-storage companion). libfprint master binds `6553` via `focaltech_moc`,
and it looks convincing — the device answers, reports `enroll_times: 12`, accepts `EnrollStart`.

It never captures. Over a 120 s enroll with the sensor being touched continuously, the finger
poll `02000280 0280` returned `02000204 000600` **2249 times with zero variation**
([`reference/usbmon-6553-no-capture.txt`](reference/usbmon-6553-no-capture.txt)). Capture has to
go through `93a9`.

## Help wanted: matching

Enrollment completes but verification returns `verify-no-match`. This has now been measured
rather than guessed — see [`tuning/`](tuning/), which runs libfprint's exact pipeline (pixman
resize → NBIS `get_minutiae` → `bozorth_to_gallery`) offline over captured frames.

**Result of a 90-combination sweep** (5 enlargement factors × 6 pre-processing variants × 3
`ppmm` values): **1–3 minutiae per frame in every single combination, and no pair ever scored
above 0.**

Control run with the same harness over libfprint's own reference captures
(`tests/*/capture.png`): **47 minutiae per frame.** The harness is fine; the frames genuinely
carry almost nothing NBIS recognises as a minutia.

Note that enrollment completing is *not* evidence matching will work — libfprint accepts a stage
when at least one minutia is found, while Bozorth needs a couple of dozen for a usable score.

Directions that look promising, given the data:

1. **Multi-frame mosaicking** — stitch several captures into a larger image before detection. The
   standard answer for small-area sensors. Implemented and measured; it raises the minutiae *count*
   but the extra minutiae are seam artifacts that do not match between independent composites.
   The cause is now isolated to the alignment metric, which lands up to 66 px off.
   Full measurements and the remaining work in [`tuning/MOSAICKING.md`](tuning/MOSAICKING.md).
2. **A non-minutiae matcher** — the vendor driver works on these same frames, which suggests
   correlation/pattern matching rather than NBIS-style minutiae.
3. **Ridge-level enhancement** (Gabor along local orientation) instead of global contrast ops.
4. Sweeping `LFSPARMS` beyond `remove_perimeter_pts`.

Measured and flat, so probably not worth repeating: plain enlargement, global
contrast/equalisation, polarity inversion, `ppmm` tweaks.

## Related work

- [`banianitc/ft9201-fingerprint-driver`](https://github.com/banianitc/ft9201-fingerprint-driver) — kernel driver, raw images
- [`armoredvortex/focaltech_2808-9e65`](https://github.com/armoredvortex/focaltech_2808-9e65) — libfprint driver for a MoC sibling
- [`jedbillyb/linux-fingerprint-drivers`](https://github.com/jedbillyb/linux-fingerprint-drivers) — community device/MR map
- libfprint [MR !572](https://gitlab.freedesktop.org/libfprint/libfprint/-/merge_requests/572) (FT9201) and [MR !554](https://gitlab.freedesktop.org/libfprint/libfprint/-/merge_requests/554) (FT9365)

## Licence

`driver/ft9201.c` is **LGPL-2.1-or-later**, matching libfprint, so it can be upstreamed as-is.
Documentation and the reference reader are provided under the same terms.

No fingerprint images are included in this repository — biometric data cannot be rotated like a
password, and `.gitignore` blocks it.
