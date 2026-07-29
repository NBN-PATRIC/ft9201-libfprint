# FocalTech FT9201 (`2808:93a9`) — USB protocol

Recovered on **2026-07-29** by capturing `usbmon` traffic from the vendor driver while it was
working, and **verified by an independent implementation**
([`reference/ft9201_read.py`](reference/ft9201_read.py)) that reads real images from the sensor
without any proprietary code.

Verification output:

```
[i] status block (32B): 02 02 02 02 02 02 02 02 …
[i] waiting for finger … presence=0x00 → presence=0x01
[i] read 5120 bytes | distinct values=256 | mean=124.4
```

Resulting frame: 64×80, 8 bpp, `min=0 max=255`, **σ ≈ 64** — sharp ridges, visible core, delta
and even pores. Ten consecutive captures gave σ between 58 and 76.

## Transport

- Interface **0**, class 255 (vendor-specific)
- Bulk **IN `0x83`** (wMaxPacketSize 32) — data
- Bulk **OUT `0x02`** (wMaxPacketSize 16) — unused on the capture path
- **No interrupt endpoint.** Several write-ups assume one exists; it does not on this revision.
- All configuration happens through **vendor control transfers**, not framed packets

(For contrast, the sibling `2808:6553` MoC function is class 220 "Diagnostic" with two 64-byte
bulk endpoints and uses a completely different, framed protocol: `02 00 <len> <cmd> … <chk>`.)

## Commands

| bmRequestType | bRequest | wValue | wIndex | Effect |
|---|---|---|---|---|
| `0xC0` | `0x43` | 0 | 0 | read **1 finger-presence byte** (`0x00` absent, `0x01` present) |
| `0x40` | `0x34` | `0x0003` | 0 | arm / trigger |
| `0x40` | `0x6F` | **`<LEN>`** | **`<ADDR>`** | **set up the next bulk read**: `LEN` bytes from `ADDR` |

### `bRequest 0x6F` — the missing piece

`0x6F` does not appear in any existing open driver.
[`banianitc/ft9201-fingerprint-driver`](https://github.com/banianitc/ft9201-fingerprint-driver)
uses `0x35` (`CONFIGURE_BULK_TRANSFER_SIZE_PROBABLY`), which arms nothing on this revision.

It encodes **length in `wValue`** and **address in `wIndex`**:

| `wValue` | `wIndex` | Content |
|---|---|---|
| `0x0020` (32) | `0x9180` | status block |
| **`0x1400` (5120)** | **`0x9080`** | **image, 64×80, 8 bpp** |
| `0x0000` | `0xFF00` | mode reset (issued before an image read) |

`0x1400` = 5120 = 64 × 80, i.e. the length is exactly width × height, so it can be parameterised
from the dimensions in AFE registers `0x14` / `0x15`.

## Sequences

**Read the status block**

```
0x40 0x34  wValue=0x0003 wIndex=0x0000     # arm
0x40 0x6F  wValue=0x0020 wIndex=0x9180     # 32 bytes @ 0x9180
bulk IN 0x83, 32 bytes
```

**Read an image** (exactly what the vendor driver does)

```
0x40 0x34  wValue=0x0003 wIndex=0x0000     # arm
0x40 0x6F  wValue=0x0000 wIndex=0xFF00     # mode reset
0x40 0x34  wValue=0x0003 wIndex=0x0000     # arm again
0x40 0x6F  wValue=0x1400 wIndex=0x9080     # 5120 bytes @ 0x9080
bulk IN 0x83, 5120 bytes                   # the image
```

**Wait for a finger**

```
loop:  0xC0 0x43 wValue=0 wIndex=0 len=1   ->  0x00 = no finger, 0x01 = finger present
```

The vendor driver polls at roughly 35 Hz (165 polls in the observed window).

## Two behaviours a driver must implement

### The sensor must be armed before it senses anything

Until a trigger + status read has happened, the presence byte stays pinned at `0x00`
**indefinitely**. The failure mode is silent and looks exactly like a broken sensor: the poll loop
runs thousands of times and nothing ever happens.

### And re-armed roughly once per second

Even after init, detection goes quiet a few seconds later. Measured in a libfprint driver:

| | presence transitions |
|---|---|
| without re-arming | **1** in 4445 polls |
| re-arming every ~1 s | **24** in a comparable window |

The vendor driver does the same — repeated `0x34` + `0x6F(0x0020, 0x9180)` + 32-byte reads appear
between presence polls in the capture.

## Image format

- **64 × 80**, 8 bits per pixel, greyscale, **no header** when read via `0x6F` / `0x9080`
- Row-major, no padding
- Light = valley, dark = ridge (no inversion needed for libfprint)

> The `banianitc` kernel driver reads `width*height + 2` and discards a 2-byte header. Through the
> `0x6F` / `0x9080` path the device returns **exactly** `width*height`, with no header.

## Dimensions

Read from AFE registers via `READ_REGISTERS` (`0x3A`): index **`0x14`** = width, **`0x15`** =
height. This unit reports 64 × 80. Reading them dynamically is preferable to hardcoding — other
revisions (FT9338W / FT9361 / FT9536w) differ, and the `banianitc` driver treats `0x60 × 0x60`
(96×96) as a separate case.

## Note on minutiae extraction

At the native 64×80 the NBIS `mindtct` used by libfprint returns **"No minutiae found"** on every
frame, even with clearly defined ridges. Enlarging the frame (3× → 192×240) before detection makes
enrollment complete. This is a property of the detector, not of the protocol.

## Reproducing

```bash
sudo systemctl stop fprintd        # fprintd holds the device
sudo python3 reference/ft9201_read.py out.png
sudo systemctl start fprintd
```

`reference/ft9201_read.py` talks to libusb through `ctypes` and has no dependencies beyond
`libusb-1.0`. Raw captures backing this document are in [`reference/`](reference/).
