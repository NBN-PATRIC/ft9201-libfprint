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

## Validating the C port

`mosaic-ctest.c` runs the driver's exact algorithm and constants over the same
PGM frames, so the port could be checked before spending finger taps on
hardware:

| implementation | frames merged | area | minutiae |
|---|---|---|---|
| Python, greedy order | 10 | 4.86x | **41** |
| C, capture order | 8 | 4.81x | **25** |

Both produce a valid composite of essentially the same size. The gap is
ordering: the Python version picks whichever remaining frame correlates best
with the composite so far, while the driver necessarily merges in the order
frames arrive, and an early poorly-matched frame drags the running mean.

The driver should land closer to the Python figure than this test suggests: its
loop counts *merged* frames, not read ones, so it keeps reading until
FT9201_BURST_FRAMES have actually been merged, whereas this test had only ten
frames on disk and stopped with eight.

Build:

```bash
gcc -O2 -o mosaic-ctest mosaic-ctest.c -lm
./mosaic-ctest ./frames out.pgm
```

## Merge order matters, and the driver now buffers for it

Merging in arrival order costs about ten minutiae, because one early poorly
matched frame drags the running mean that later frames are correlated against.
`mosaic-ctest-greedy.c` is the same C code with the frames buffered first and
each round taking whichever remaining frame correlates best:

| merge strategy | frames merged | area | minutiae |
|---|---|---|---|
| C, arrival order | 8 | 4.81x | 25 |
| **C, greedy** | **10** | **4.89x** | **35** |
| Python, greedy | 10 | 4.86x | 41 |

Cost is not a problem: 0.29 s for a ten-frame greedy merge (~29 ms per frame)
against 0.07 s for arrival order, so it runs inline in the driver without any
risk to the event loop.

The driver now buffers the whole burst (10 x 5120 B = 51 KB) and merges
greedily once it is complete.
