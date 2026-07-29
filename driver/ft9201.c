/*
 * FocalTech FT9201 fingerprint sensor driver for libfprint
 * Copyright (C) 2026 Patric Farias <patric.womp@gmail.com>
 *
 * USB 2808:93a9 — image sensor, matching done on the host.
 *
 * Protocol was recovered by capturing USB traffic (usbmon) from the vendor
 * driver and re-implemented independently; see PROTOCOLO-93a9.md.
 *
 *   0xC0 0x43  value=0      index=0        len=1   -> finger presence byte
 *   0x40 0x34  value=0x0003 index=0                -> arm/trigger
 *   0x40 0x6F  value=<LEN>  index=<ADDR>           -> set up next bulk read
 *
 *   0x9180 / 0x0020 -> 32-byte status block
 *   0x9080 / 0x1400 -> 64x80 8bpp image
 *   0xFF00 / 0x0000 -> mode reset (issued before an image read)
 *
 * Note: request 0x6F is what makes reads work on this revision. Existing
 * out-of-tree drivers use 0x35, which arms nothing here — the likely cause of
 * the long-standing "initialises but never reads" reports.
 *
 * This library is free software; you can redistribute it and/or modify it
 * under the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at your
 * option) any later version.
 *
 * This library is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License
 * for more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with this library; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

#define FP_COMPONENT "ft9201"

#include "drivers_api.h"

/* Sensor geometry. The AFE registers 0x14/0x15 carry width/height; this
 * revision reports 64x80 and that is what the image address expects. */
#define FT9201_IMG_WIDTH   64
#define FT9201_IMG_HEIGHT  80
#define FT9201_IMG_SIZE    (FT9201_IMG_WIDTH * FT9201_IMG_HEIGHT)

#define FT9201_EP_IN       (0x03 | FPI_USB_ENDPOINT_IN)

/* Pixels per millimetre. The frame is 64x80 over roughly 8x10 mm, so the core
 * upscales it to the ~500 dpi (19.7 ppmm) that minutiae detection assumes. */
/* Enlargement factor applied before minutiae detection (64x80 -> 192x240). */
#define FT9201_ENLARGE     3

/* Vendor requests */
#define FT9201_REQ_PRESENCE    0x43
#define FT9201_REQ_TRIGGER     0x34
#define FT9201_REQ_SETUP_BULK  0x6F

/* Addresses used with FT9201_REQ_SETUP_BULK */
#define FT9201_ADDR_IMAGE      0x9080
#define FT9201_ADDR_STATUS     0x9180
#define FT9201_ADDR_RESET      0xFF00

#define FT9201_STATUS_LEN      0x0020

#define FT9201_TRIGGER_VALUE   0x0003

#define FT9201_CTRL_TIMEOUT    1000
#define FT9201_BULK_TIMEOUT    3000

/* The vendor driver polls presence at roughly 35 Hz. */
#define FT9201_POLL_INTERVAL   30

/* Consecutive "no finger" reads before we report the finger as gone. Single
 * samples flicker while the finger is being lifted. */
#define FT9201_ABSENT_DEBOUNCE 3

/* Re-arm the sensor roughly once per second (30 polls x 30 ms). Without this the
 * presence byte goes quiet a moment after the initial arm and no touch is ever
 * seen again. */
#define FT9201_REARM_EVERY     30

struct _FpiDeviceFt9201
{
  FpImageDevice        parent;

  gboolean             active;
  guint                absent_run;
  guint8               last_presence;
  guint                polls_since_arm;

  /* State requested by the FpImageDevice core through ->change_state(). The
   * capture SSM follows it instead of running a loop of its own. */
  FpiImageDeviceState  dev_state;
};

G_DECLARE_FINAL_TYPE (FpiDeviceFt9201, fpi_device_ft9201, FPI, DEVICE_FT9201, FpImageDevice)
G_DEFINE_TYPE (FpiDeviceFt9201, fpi_device_ft9201, FP_TYPE_IMAGE_DEVICE)

enum capture_states {
  /* The sensor does not start reporting presence until it has been armed once
   * and its status block has been read. Skipping this leaves 0x43 pinned at
   * 0x00 forever, which looks exactly like "the finger is never detected". */
  CAPTURE_INIT_ARM,
  CAPTURE_INIT_SET_STATUS,
  CAPTURE_INIT_READ_STATUS,

  CAPTURE_POLL_PRESENCE,
  CAPTURE_ARM_RESET,
  CAPTURE_SET_RESET,
  CAPTURE_ARM_IMAGE,
  CAPTURE_SET_IMAGE,
  CAPTURE_READ_IMAGE,
  CAPTURE_WAIT_LIFT,
  CAPTURE_NUM_STATES,
};

/* ------------------------------------------------------------------ helpers */

static FpiUsbTransfer *
ft9201_ctrl_out (FpDevice *dev, FpiSsm *ssm, guint8 request, guint16 value, guint16 idx)
{
  FpiUsbTransfer *transfer = fpi_usb_transfer_new (dev);

  transfer->ssm = ssm;
  fpi_usb_transfer_fill_control (transfer,
                                 G_USB_DEVICE_DIRECTION_HOST_TO_DEVICE,
                                 G_USB_DEVICE_REQUEST_TYPE_VENDOR,
                                 G_USB_DEVICE_RECIPIENT_DEVICE,
                                 request, value, idx, 0);
  return transfer;
}

static void
ft9201_submit_ctrl_out (FpDevice *dev, FpiSsm *ssm, guint8 request, guint16 value, guint16 idx)
{
  FpiUsbTransfer *transfer = ft9201_ctrl_out (dev, ssm, request, value, idx);

  fpi_usb_transfer_submit (transfer, FT9201_CTRL_TIMEOUT, NULL,
                           fpi_ssm_usb_transfer_cb, NULL);
}

/* ------------------------------------------------------------ capture states */

static void
presence_cb (FpiUsbTransfer *transfer, FpDevice *device,
             gpointer user_data, GError *error)
{
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (device);
  FpImageDevice *imgdev = FP_IMAGE_DEVICE (device);
  guint8 present;

  if (error)
    {
      fpi_ssm_mark_failed (transfer->ssm, error);
      return;
    }

  if (transfer->actual_length < 1)
    {
      fpi_ssm_mark_failed (transfer->ssm,
                           fpi_device_error_new_msg (FP_DEVICE_ERROR_PROTO,
                                                     "short presence read"));
      return;
    }

  present = transfer->buffer[0];

  if (present != self->last_presence)
    {
      fp_dbg ("presence byte: 0x%02x -> 0x%02x", self->last_presence, present);
      self->last_presence = present;
    }

  switch (self->dev_state)
    {
    case FPI_IMAGE_DEVICE_STATE_AWAIT_FINGER_ON:
      if (present != 0x00)
        {
          self->absent_run = 0;
          fpi_image_device_report_finger_status (imgdev, TRUE);
          /* The core switches us to CAPTURE; the SSM picks it up below. */
        }
      break;

    case FPI_IMAGE_DEVICE_STATE_AWAIT_FINGER_OFF:
      /* The presence byte pulses rather than staying high, so a single 0x00
       * does not mean the finger left. Require a few consecutive reads. */
      if (present == 0x00)
        {
          if (++self->absent_run >= FT9201_ABSENT_DEBOUNCE)
            {
              self->absent_run = 0;
              fpi_image_device_report_finger_status (imgdev, FALSE);
            }
        }
      else
        {
          self->absent_run = 0;
        }
      break;

    case FPI_IMAGE_DEVICE_STATE_CAPTURE:
    case FPI_IMAGE_DEVICE_STATE_IDLE:
    case FPI_IMAGE_DEVICE_STATE_INACTIVE:
    case FPI_IMAGE_DEVICE_STATE_ACTIVATING:
    case FPI_IMAGE_DEVICE_STATE_DEACTIVATING:
      break;
    }

  if (self->dev_state == FPI_IMAGE_DEVICE_STATE_CAPTURE)
    {
      /* Core wants a frame now. */
      fpi_ssm_next_state (transfer->ssm);
      return;
    }

  if (!self->active)
    {
      fpi_ssm_mark_completed (transfer->ssm);
      return;
    }

  /* Presence detection goes stale if the sensor is left alone: the byte stops
   * moving after a while and no touch is ever reported again. The vendor driver
   * re-arms (trigger + status read) roughly once per second, so do the same. */
  if (++self->polls_since_arm >= FT9201_REARM_EVERY)
    {
      self->polls_since_arm = 0;
      fpi_ssm_jump_to_state_delayed (transfer->ssm, CAPTURE_INIT_ARM,
                                     FT9201_POLL_INTERVAL);
      return;
    }

  fpi_ssm_jump_to_state_delayed (transfer->ssm, CAPTURE_POLL_PRESENCE,
                                 FT9201_POLL_INTERVAL);
}

static void
image_cb (FpiUsbTransfer *transfer, FpDevice *device,
          gpointer user_data, GError *error)
{
  FpImageDevice *imgdev = FP_IMAGE_DEVICE (device);
  g_autoptr(FpImage) raw = NULL;
  FpImage *img;

  if (error)
    {
      fpi_ssm_mark_failed (transfer->ssm, error);
      return;
    }

  if (transfer->actual_length != FT9201_IMG_SIZE)
    {
      fpi_ssm_mark_failed (transfer->ssm,
                           fpi_device_error_new_msg (FP_DEVICE_ERROR_PROTO,
                                                     "short image: got %" G_GSIZE_FORMAT
                                                     " of %d bytes",
                                                     transfer->actual_length,
                                                     FT9201_IMG_SIZE));
      return;
    }

  raw = fp_image_new (FT9201_IMG_WIDTH, FT9201_IMG_HEIGHT);
  memcpy (raw->data, transfer->buffer, FT9201_IMG_SIZE);

  /* 64x80 is far too small for NBIS: mindtct returns "no minutiae found" even
   * on frames with clearly defined ridges. Enlarging the frame gives the
   * detector the pixel density it assumes (~500 dpi), the same trick the
   * egis0570/elanspi/aes3k drivers use for their small sensors. */
  img = fpi_image_resize (raw, FT9201_ENLARGE, FT9201_ENLARGE);

  /* The sensor already returns ridges dark on a light background, which is what
   * the internal binarisation expects — no inversion needed. */
  fpi_image_device_image_captured (imgdev, img);

  fpi_ssm_next_state (transfer->ssm);
}

static void
capture_run_state (FpiSsm *ssm, FpDevice *dev)
{
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (dev);
  FpiUsbTransfer *transfer;

  switch (fpi_ssm_get_cur_state (ssm))
    {
    case CAPTURE_INIT_ARM:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_TRIGGER,
                              FT9201_TRIGGER_VALUE, 0x0000);
      break;

    case CAPTURE_INIT_SET_STATUS:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_SETUP_BULK,
                              FT9201_STATUS_LEN, FT9201_ADDR_STATUS);
      break;

    case CAPTURE_INIT_READ_STATUS:
      transfer = fpi_usb_transfer_new (dev);
      transfer->ssm = ssm;
      fpi_usb_transfer_fill_bulk (transfer, FT9201_EP_IN, FT9201_STATUS_LEN);
      fpi_usb_transfer_submit (transfer, FT9201_BULK_TIMEOUT, NULL,
                               fpi_ssm_usb_transfer_cb, NULL);
      break;

    case CAPTURE_POLL_PRESENCE:
      transfer = fpi_usb_transfer_new (dev);
      transfer->ssm = ssm;
      fpi_usb_transfer_fill_control (transfer,
                                     G_USB_DEVICE_DIRECTION_DEVICE_TO_HOST,
                                     G_USB_DEVICE_REQUEST_TYPE_VENDOR,
                                     G_USB_DEVICE_RECIPIENT_DEVICE,
                                     FT9201_REQ_PRESENCE, 0, 0, 1);
      fpi_usb_transfer_submit (transfer, FT9201_CTRL_TIMEOUT, NULL,
                               presence_cb, NULL);
      break;

    /* The vendor driver arms, resets the mode, arms again, then points the
     * bulk read at the image buffer. Reproduced verbatim: skipping the reset
     * yields a stale frame on the second and later captures. */
    case CAPTURE_ARM_RESET:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_TRIGGER,
                              FT9201_TRIGGER_VALUE, 0x0000);
      break;

    case CAPTURE_SET_RESET:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_SETUP_BULK,
                              0x0000, FT9201_ADDR_RESET);
      break;

    case CAPTURE_ARM_IMAGE:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_TRIGGER,
                              FT9201_TRIGGER_VALUE, 0x0000);
      break;

    case CAPTURE_SET_IMAGE:
      ft9201_submit_ctrl_out (dev, ssm, FT9201_REQ_SETUP_BULK,
                              FT9201_IMG_SIZE, FT9201_ADDR_IMAGE);
      break;

    case CAPTURE_READ_IMAGE:
      transfer = fpi_usb_transfer_new (dev);
      transfer->ssm = ssm;
      fpi_usb_transfer_fill_bulk (transfer, FT9201_EP_IN, FT9201_IMG_SIZE);
      fpi_usb_transfer_submit (transfer, FT9201_BULK_TIMEOUT, NULL,
                               image_cb, NULL);
      break;

    case CAPTURE_WAIT_LIFT:
      /* Frame delivered. Hand control back to the presence poll, which now
       * follows whatever state the core moved us to (usually FINGER_OFF). */
      if (self->active)
        fpi_ssm_jump_to_state_delayed (ssm, CAPTURE_POLL_PRESENCE,
                                       FT9201_POLL_INTERVAL);
      else
        fpi_ssm_mark_completed (ssm);
      break;

    default:
      g_assert_not_reached ();
    }
}

static void
capture_complete (FpiSsm *ssm, FpDevice *dev, GError *error)
{
  FpImageDevice *imgdev = FP_IMAGE_DEVICE (dev);
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (dev);

  if (error && !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    {
      fpi_image_device_session_error (imgdev, error);
      return;
    }

  g_clear_error (&error);

  if (!self->active)
    fpi_image_device_deactivate_complete (imgdev, NULL);
}

/* -------------------------------------------------------------- lifecycle */

static void
dev_change_state (FpImageDevice *dev, FpiImageDeviceState state)
{
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (dev);

  fp_dbg ("change_state: %d -> %d", self->dev_state, state);
  self->dev_state = state;
  self->absent_run = 0;
}

static void
dev_activate (FpImageDevice *dev)
{
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (dev);
  FpiSsm *ssm;

  self->active = TRUE;
  self->absent_run = 0;
  self->last_presence = 0xFF;   /* forces the first read to be logged */
  self->dev_state = FPI_IMAGE_DEVICE_STATE_AWAIT_FINGER_ON;
  self->polls_since_arm = 0;

  ssm = fpi_ssm_new (FP_DEVICE (dev), capture_run_state, CAPTURE_NUM_STATES);
  fpi_ssm_start (ssm, capture_complete);

  fpi_image_device_activate_complete (dev, NULL);
}

static void
dev_deactivate (FpImageDevice *dev)
{
  FpiDeviceFt9201 *self = FPI_DEVICE_FT9201 (dev);

  /* The state machine notices and completes, which calls
   * fpi_image_device_deactivate_complete(). */
  self->active = FALSE;
}

static void
dev_open (FpImageDevice *dev)
{
  GError *error = NULL;

  if (!g_usb_device_claim_interface (fpi_device_get_usb_device (FP_DEVICE (dev)),
                                     0, 0, &error))
    {
      fpi_image_device_open_complete (dev, error);
      return;
    }

  fpi_image_device_open_complete (dev, NULL);
}

static void
dev_close (FpImageDevice *dev)
{
  GError *error = NULL;

  g_usb_device_release_interface (fpi_device_get_usb_device (FP_DEVICE (dev)),
                                  0, 0, &error);
  fpi_image_device_close_complete (dev, error);
}

static const FpIdEntry id_table[] = {
  { .vid = 0x2808, .pid = 0x93a9, },
  { .vid = 0,      .pid = 0,      },
};

static void
fpi_device_ft9201_init (FpiDeviceFt9201 *self)
{
}

static void
fpi_device_ft9201_class_init (FpiDeviceFt9201Class *klass)
{
  FpDeviceClass *dev_class = FP_DEVICE_CLASS (klass);
  FpImageDeviceClass *img_class = FP_IMAGE_DEVICE_CLASS (klass);

  dev_class->id = "ft9201";
  dev_class->full_name = "FocalTech FT9201";
  dev_class->type = FP_DEVICE_TYPE_USB;
  dev_class->id_table = id_table;
  dev_class->scan_type = FP_SCAN_TYPE_PRESS;

  img_class->img_open = dev_open;
  img_class->img_close = dev_close;
  img_class->activate = dev_activate;
  img_class->deactivate = dev_deactivate;
  img_class->change_state = dev_change_state;

  img_class->img_width = FT9201_IMG_WIDTH;
  img_class->img_height = FT9201_IMG_HEIGHT;

  /* 64x80 is a small sensor; the default threshold rejects usable frames. */
  img_class->bz3_threshold = 20;
}
