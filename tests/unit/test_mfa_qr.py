"""Guards on the enrolment QR: what a phone scans must be what the server states.

The provisioning URI is built in services/api/app/mfa.py from the same constants the verifier reads,
and the QR is now rendered from that string rather than typed by hand -- which makes the image part
of the second-factor contract. So the checks below read the PNG back with an independent decoder
(zxing-cpp, no shared code with segno) and demand the exact URI, and the ones after that keep the
delivery honest: the shipped CSP must still allow the data: URI the page assigns to <img src>, and
the page must assign it to `src` rather than injecting markup it got from the wire.

Each detector ships with the sample that must make it fire.
"""
import base64
import pathlib
import re
import struct
import sys
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "services" / "api"))

import e2e_client  # noqa: E402
from app.mfa import provisioning_uri, qr_data_uri  # noqa: E402

APP_JS = ROOT / "services/web/app.js"
WEB_NGINX = ROOT / "services/web/nginx.conf"
ADMIN_NGINX = ROOT / "services/admin/nginx.conf"

SECRET = "JBSWY3DPEHPK3PXP"
URI = provisioning_uri(SECRET, "probe@example.local")


def ihdr(png: bytes):
    (width, height, depth, colour, _comp, _filt, interlace) = struct.unpack(">IIBBBBB", png[16:29])
    return width, height, depth, colour, interlace


def chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


# ------------------------------------------------------------------ the payload ----

def test_the_rendered_qr_decodes_back_to_the_provisioning_uri():
    assert e2e_client.decode_qr_data_uri(qr_data_uri(URI)) == URI


def test_the_decoder_is_actually_reading_and_not_answering_the_question():
    """The control the check above needs: a symbol for a different seed must not read as this one."""
    other = provisioning_uri("KBSWY3DPEHPK3PXP", "probe@example.local")
    assert other != URI
    assert e2e_client.decode_qr_data_uri(qr_data_uri(other)) == other
    assert e2e_client.decode_qr_data_uri(qr_data_uri(other)) != URI


def test_the_uri_states_the_parameters_the_verifier_uses():
    """A QR that scans into an authenticator with no `period`/`digits` is a coincidence waiting to
    break, and the URI builder already refuses to rely on the defaults; the image inherits it."""
    decoded = e2e_client.decode_qr_data_uri(qr_data_uri(URI))
    assert "period=30" in decoded and "digits=6" in decoded, decoded


@pytest.mark.parametrize("scale", [2, 3, 4, 6])
def test_the_symbol_survives_its_rendering_scale(scale):
    assert e2e_client.decode_qr_data_uri(qr_data_uri(URI, scale=scale)) == URI


# ------------------------------------------------------------------ the container ----

def test_the_image_is_a_plain_greyscale_png_at_the_module_grid_size():
    """The page sizes the <img> in CSS, so the raster itself has to carry the whole symbol: modules
    plus the quiet zone, times the scale. A width that disagrees means the image a phone sees is not
    the image that was verified."""
    data_uri = qr_data_uri(URI, scale=4)
    assert data_uri.startswith("data:image/png;base64,")
    png = base64.b64decode(data_uri.split(",", 1)[1])
    width, height, depth, colour, interlace = ihdr(png)
    assert (depth, colour, interlace) == (1, 0, 0), "segno changed its PNG profile; re-read the decoder"
    assert width == height and width % 4 == 0
    import segno
    symbol = segno.make(URI, error="m").symbol_size()
    assert (width // 4, height // 4) == symbol, f"raster {width} vs symbol {symbol}"


def test_a_corrupted_png_is_refused_rather_than_misread():
    data_uri = qr_data_uri(URI)
    png = bytearray(base64.b64decode(data_uri.split(",", 1)[1]))
    png[-30] ^= 0xFF
    with pytest.raises(ValueError, match="CRC"):
        e2e_client.png_to_gray(bytes(png))


def test_the_decoder_refuses_a_profile_it_has_not_been_written_for():
    """Silence here would let a future writer change colour type and the round trip quietly stop
    meaning anything."""
    header = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 1))
              + chunk(b"IEND", b""))
    with pytest.raises(ValueError, match="unsupported PNG"):
        e2e_client.png_to_gray(header + b"\x00\x00\x00\x00IEND" + b"\xae\x42\x60\x82")


# ------------------------------------------------------------------ the delivery ----

def test_the_shipped_csp_still_allows_a_data_uri_image():
    """The QR is delivered as a data: URI because the policy allows it. If `img-src` ever loses
    `data:`, every enrolment panel shows a broken image and only a browser would notice."""
    for path in (WEB_NGINX, ADMIN_NGINX):
        policy = re.search(r"Content-Security-Policy \"([^\"]+)\"", path.read_text(encoding="utf-8")).group(1)
        img_src = re.search(r"img-src([^;]*)", policy).group(1)
        assert "data:" in img_src, f"{path.name}: img-src lost data:, the enrolment QR cannot load ({img_src.strip()})"
        assert "script-src 'self'" in policy, f"{path.name}: script-src widened, so a vendored encoder was not needed"


def test_the_page_assigns_the_image_source_and_never_injects_it():
    app = APP_JS.read_text(encoding="utf-8")
    block = re.search(r"const qr = \$\('#mfaQr'\);.*?\n    \}", app, re.DOTALL)
    assert block, "the enrolment QR handler is no longer readable in app.js"
    assert "qr.src = data.qr_png_data_uri" in block.group(0), block.group(0)[:160]
    assert "innerHTML" not in block.group(0), "wire data must not reach innerHTML"


def test_the_image_carries_alt_text_and_is_hidden_until_there_is_one():
    index = (ROOT / "services/web/index.html").read_text(encoding="utf-8")
    tag = re.search(r"<img id=\"mfaQr\"[^>]*>", index).group(0)
    assert 'alt="' in tag and 'alt=""' not in tag, f"the QR would be announced as decoration: {tag}"
    assert "hidden" in tag, "an empty image frame is shown before the enrolment response arrives"
