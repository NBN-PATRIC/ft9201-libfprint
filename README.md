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
| **Verification matches** | ❌ **no** — two matcher approaches measured and failed, see [`MATCHING.md`](MATCHING.md) |

So: **this is not a finished driver, and matching is not a calibration problem.** NBIS
minutiae are a dead end here — at most 3 per frame, no match in 90 parameter combinations.
Correlation over subtemplates does better than that: against impostors captured on this same
sensor it separates *perfectly* (d′ = 3.28) **when the finger is placed the same way as at
enrolment**, and collapses into the impostor range as soon as it is rotated. Denser enrolment,
Gabor ridge enhancement and a larger scoring window were each measured and none recovers it.
The missing piece is a rotation-invariant representation. Full account in
[`MATCHING.md`](MATCHING.md).

What *is* solid is the capture side: the protocol is complete and verified, image acquisition
is good, and ridge structure is resolved at an 8–12 px period. That part should be immediately
useful to anyone stuck on this sensor.

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
3. **Enlarge before minutiae detection — by 2×, not 3×.** At the native 64×80, NBIS `mindtct`
   returns *"No minutiae found"* on every frame, even with clearly defined ridges (σ ≈ 64, full
   dynamic range): the image is too small for its block grid, the same problem `egis0570` /
   `elanspi` / `aes3k` solve the same way. But the measured ridge period here is 8–14 px, already
   what NBIS expects at ~500 dpi, so scaling too far moves the period out of the band it can
   process. Measured over ten frames: **1× → 1.7 minutiae** on average (7/10 frames with any),
   **2× → 2.9** (9/10), **3× → 1.0** (2/10). An earlier revision of this driver used 3×, picked
   before the ridge period was known.

## Layout

```
PROTOCOL.md                     protocol specification (verified)
MATCHING.md                     why matching does not work here, measured
reference/ft9201_read.py        independent reader — libusb via ctypes, no deps
reference/usbmon-*.txt          USB captures backing the spec
driver/ft9201.c                 libfprint FpImageDevice driver (LGPL-2.1+)
driver/ft9201-match.{c,h}       correlation matcher — measured insufficient, kept for reference
driver/meson-integration.patch  how to add it to the libfprint tree
tools/ft9201-mode               switch system / dev / free, with a guaranteed way back
tools/sessao/                   multi-finger capture sessions
tuning/                         offline harnesses and the captured measurements
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

Enrollment completes but verification returns `verify-no-match`, and this is now measured rather
than guessed — see [`MATCHING.md`](MATCHING.md) for the full account and [`tuning/`](tuning/)
for the harnesses, which run libfprint's exact pipeline (pixman resize → NBIS `get_minutiae` →
`bozorth_to_gallery`) offline over captured frames.

**Result of a 90-combination sweep** (5 enlargement factors × 6 pre-processing variants × 3
`ppmm` values): **1–3 minutiae per frame in every single combination, and no pair ever scored
above 0.**

Control run with the same harness over libfprint's own reference captures
(`tests/*/capture.png`): **47 minutiae per frame.** The harness is fine; the frames genuinely
carry almost nothing NBIS recognises as a minutia.

Note that enrollment completing is *not* evidence matching will work — libfprint accepts a stage
when at least one minutia is found, while Bozorth needs a couple of dozen for a usable score.

Everything below has been measured. The list is kept as a record of what was tried, because
each entry is a direction not worth repeating.

1. **Multi-frame mosaicking** — the standard answer for small-area sensors. It raises the
   minutiae *count*, but the extra minutiae are seam artifacts: two independent composites of one
   finger, 16 minutiae each, score **zero** against each other, and ridge quality drops from 1.10
   in the inputs to 0.42 in the composite. Blocked on sub-degree rigid registration.
   See [`tuning/MOSAICKING.md`](tuning/MOSAICKING.md).
2. **A non-minutiae matcher** — the vendor library works on these same frames and its strings show
   it keeps multiple subtemplates per finger. Implemented as normalised cross-correlation over
   subtemplates with a rotation search. It separates perfectly (d′ = 3.28) when the probe is
   nearly pixel-identical to an enrolled view, and in **natural use** gives genuine 0.687 against
   impostor 0.680 — **d′ = 0.06**. Measured ridge orientation across eleven casual touches spans
   0°–165°, so on a window this small there is no typical presentation to enrol against.
3. **Match on chip** — ruled out for this device. The system library's id table contains only
   `93a9`, bound to the plain `focaltech` driver, with no `6553` entry: the companion chip is not
   in the matching path, and `focaltech:moc` is for other models. The vendor matches on the host,
   on exactly these frames.
4. **Ridge-level enhancement** — Gabor along the local orientation field (coherence 0.77–0.87, so
   the field is confident). d′ stays at 0.19–0.30 and goes negative in two variants: enhancement
   makes every print look like clean parallel ridges, which at this scale makes different fingers
   look *more* alike.
5. **A rotation-invariant descriptor** (log-polar, Fourier-Mellin) — do not build one without
   running the control first. Rotating a sample *synthetically* is recovered to **0.98** at every
   angle from 0° to 90°: the rotation search already works. What defeats a physically rotated
   finger is that a 3.2 × 4.1 mm window then sees a different patch of skin.

Measured and flat, so not worth repeating: plain enlargement beyond 2×, global
contrast/equalisation, polarity inversion, `ppmm` tweaks, fixed-pattern-noise removal
(d′ 0.14 → 0.24), widening the rotation search to ±90° (raises both distributions equally), and
scoring windows from 24 to 48 px (d′ 0.04 → 0.14 → −0.17 — area is not the bottleneck).

The sensor is not the limitation: the vendor library authenticates on this same hardware. Closing
the gap means replicating a proprietary feature extractor, which is a research project rather
than a tuning exercise. Full account and every number in [`MATCHING.md`](MATCHING.md).

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
