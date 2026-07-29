# Multi-frame mosaicking — results (2026-07-29)

Ten frames of the same finger, captured with batch-capture.py.

| input | area | minutiae |
|---|---|---|
| single frame (64x80) | 1.00x | **1-3** |
| mosaic, 6 frames | 2.89x | **16** |
| mosaic, 10 frames (168x148) | 4.86x | **41** |
| libfprint reference captures | - | 47 |

Best pre-processing on the mosaic: 2x enlargement + invert + equalize.

## Matching

Split test: frames 1-5 -> mosaic A, frames 6-10 -> mosaic B, then matched.
First non-zero Bozorth score of the whole effort (3), but far from the
threshold of 40. Cause is clear: five frames only build a 2.6-4.0x mosaic
with ~16 minutiae, against 41 for the full ten.

## What this implies for the driver

Accumulate frames while the finger is down, mosaic them, and report a
single composite image to libfprint instead of one raw 64x80 frame.
Needs roughly 10+ frames per composite, so the capture loop has to keep
reading during a press rather than returning after the first frame.
