#!/usr/bin/env python3
"""
Leitor independente do FocalTech FT9201 (2808:93a9) — implementação do protocolo
descoberto por captura usbmon do driver proprietário em 2026-07-29.

Protocolo (control transfers vendor, bmRequestType 0x40 OUT / 0xc0 IN):

    0xc0 0x43  wValue=0      wIndex=0       len=1     -> byte de presença de dedo
    0x40 0x34  wValue=0x0003 wIndex=0                 -> arma/dispara
    0x40 0x6f  wValue=<LEN>  wIndex=<ADDR>            -> configura leitura bulk
    bulk IN 0x83 <LEN> bytes                          -> lê

Endereços conhecidos:
    0x9180 + len 0x0020 (32)   -> bloco de status
    0x9080 + len 0x1400 (5120) -> imagem 64x80, 8 bpp

Uso: sudo python3 ft9201_read.py [saida.png]
     (o fprintd precisa estar parado — ele segura o device)
"""
import ctypes as ct
import sys
import time

VID, PID = 0x2808, 0x93A9
EP_IN = 0x83
IMG_W, IMG_H = 64, 80
IMG_LEN = IMG_W * IMG_H          # 5120
ADDR_IMAGE, ADDR_STATUS = 0x9080, 0x9180

REQ_PRESENCE = 0x43
REQ_TRIGGER = 0x34
REQ_SETUP_BULK = 0x6F

lib = ct.CDLL("libusb-1.0.so.0")


class Ctx(ct.Structure):
    pass


ctx_p = ct.POINTER(Ctx)
dev_p = ct.c_void_p

lib.libusb_init.argtypes = [ct.POINTER(ctx_p)]
lib.libusb_open_device_with_vid_pid.restype = dev_p
lib.libusb_open_device_with_vid_pid.argtypes = [ctx_p, ct.c_uint16, ct.c_uint16]
lib.libusb_control_transfer.argtypes = [
    dev_p, ct.c_uint8, ct.c_uint8, ct.c_uint16, ct.c_uint16,
    ct.POINTER(ct.c_ubyte), ct.c_uint16, ct.c_uint,
]
lib.libusb_bulk_transfer.argtypes = [
    dev_p, ct.c_ubyte, ct.POINTER(ct.c_ubyte), ct.c_int,
    ct.POINTER(ct.c_int), ct.c_uint,
]
lib.libusb_strerror.restype = ct.c_char_p


def err(code):
    try:
        return lib.libusb_strerror(ct.c_int(code)).decode()
    except Exception:
        return str(code)


class FT9201:
    def __init__(self):
        self.ctx = ctx_p()
        if lib.libusb_init(ct.byref(self.ctx)) != 0:
            raise RuntimeError("libusb_init falhou")
        self.h = lib.libusb_open_device_with_vid_pid(self.ctx, VID, PID)
        if not self.h:
            raise RuntimeError(f"sensor {VID:04x}:{PID:04x} nao encontrado "
                               "(rode como root e com o fprintd parado)")
        lib.libusb_set_auto_detach_kernel_driver(self.h, 1)
        rc = lib.libusb_claim_interface(self.h, 0)
        if rc != 0:
            raise RuntimeError(f"claim_interface falhou: {err(rc)} "
                               "(o fprintd ainda esta segurando o device?)")

    def ctrl_out(self, req, value, index):
        rc = lib.libusb_control_transfer(self.h, 0x40, req, value, index, None, 0, 1000)
        if rc < 0:
            raise RuntimeError(f"control OUT req=0x{req:02x} falhou: {err(rc)}")

    def ctrl_in(self, req, value, index, length):
        buf = (ct.c_ubyte * length)()
        rc = lib.libusb_control_transfer(self.h, 0xC0, req, value, index, buf, length, 1000)
        if rc < 0:
            raise RuntimeError(f"control IN req=0x{req:02x} falhou: {err(rc)}")
        return bytes(buf[:rc])

    def bulk_in(self, length, timeout=3000):
        buf = (ct.c_ubyte * length)()
        got = ct.c_int(0)
        rc = lib.libusb_bulk_transfer(self.h, EP_IN, buf, length, ct.byref(got), timeout)
        if rc < 0:
            raise RuntimeError(f"bulk IN falhou: {err(rc)}")
        return bytes(buf[:got.value])

    def finger_present(self):
        return self.ctrl_in(REQ_PRESENCE, 0, 0, 1)[0]

    def read_block(self, addr, length):
        """Sequência exata observada no driver proprietário."""
        self.ctrl_out(REQ_TRIGGER, 0x0003, 0x0000)
        self.ctrl_out(REQ_SETUP_BULK, length, addr)
        return self.bulk_in(length)

    def read_status(self):
        return self.read_block(ADDR_STATUS, 32)

    def read_image(self):
        # o driver proprietário intercala um reset (0x6f wValue=0 wIndex=0xff00)
        self.ctrl_out(REQ_TRIGGER, 0x0003, 0x0000)
        self.ctrl_out(REQ_SETUP_BULK, 0x0000, 0xFF00)
        return self.read_block(ADDR_IMAGE, IMG_LEN)

    def close(self):
        try:
            lib.libusb_release_interface(self.h, 0)
            lib.libusb_close(self.h)
        except Exception:
            pass


def save(data, path):
    if len(data) < IMG_LEN:
        data = data + b"\x00" * (IMG_LEN - len(data))
    try:
        from PIL import Image
        Image.frombytes("L", (IMG_W, IMG_H), data[:IMG_LEN]).save(path)
        return path
    except Exception as e:
        pgm = path.rsplit(".", 1)[0] + ".pgm"
        with open(pgm, "wb") as f:
            f.write(b"P5\n%d %d\n255\n" % (IMG_W, IMG_H))
            f.write(data[:IMG_LEN])
        print(f"  (PIL indisponivel: {e}) -> salvo como {pgm}")
        return pgm


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ft9201.png"
    d = FT9201()
    print(f"[i] sensor {VID:04x}:{PID:04x} aberto")
    try:
        st = d.read_status()
        print(f"[i] bloco de status ({len(st)}B): {st[:16].hex(' ')}")

        print("[i] aguardando dedo (30s)... ENCOSTE AGORA")
        t0 = time.time()
        seen = None
        while time.time() - t0 < 30:
            v = d.finger_present()
            if seen is None or v != seen:
                print(f"    presenca=0x{v:02x}")
                seen = v
            if v not in (0x00,):
                break
            time.sleep(0.05)

        img = d.read_image()
        n = len(img)
        uniq = len(set(img))
        mean = sum(img) / max(1, n)
        print(f"[i] lidos {n} bytes | valores distintos={uniq} | media={mean:.1f}")
        if uniq < 5:
            print("[!] imagem praticamente uniforme — provavelmente sem dedo no sensor")
        p = save(img, out)
        print(f"[=] imagem salva em {p}")
    finally:
        d.close()


if __name__ == "__main__":
    main()
