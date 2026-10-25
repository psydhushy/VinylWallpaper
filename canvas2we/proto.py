"""Minimal protobuf codec for Spotify's canvaz-cache endpoint.

Hand-rolled so the app doesn't need protoc or generated code. Schema
(from librespot's protocol/proto/canvaz.proto):

    message EntityCanvazRequest {
        message Entity { string entity_uri = 1; string etag = 2; }
        repeated Entity entities = 1;
    }
    message EntityCanvazResponse {
        message Canvaz {
            string id = 1; string url = 2; string file_id = 3; Type type = 4;
            string entity_uri = 5; Artist artist = 6; bool explicit = 7;
            string uploaded_by = 8; string etag = 9; string canvas_uri = 11;
            string storylines_id = 12;
        }
        repeated Canvaz canvases = 1;
        int64 ttl_in_seconds = 2;
    }
    message Artist { string uri = 1; string name = 2; string avatar = 3; }
    enum Type { IMAGE = 0; VIDEO = 1; VIDEO_LOOPING = 2; VIDEO_LOOPING_RANDOM = 3; GIF = 4; }
"""

from __future__ import annotations

from dataclasses import dataclass

CANVAS_TYPES = {0: "IMAGE", 1: "VIDEO", 2: "VIDEO_LOOPING", 3: "VIDEO_LOOPING_RANDOM", 4: "GIF"}


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _len_field(field: int, payload: bytes) -> bytes:
    return encode_varint((field << 3) | 2) + encode_varint(len(payload)) + payload


def decode_message(buf: bytes) -> dict[int, list]:
    """Decode one message level into {field_number: [values]}.

    Varints become ints, length-delimited fields stay bytes (decode nested
    messages by calling this again).
    """
    fields: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, i = read_varint(buf, i)
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        elif wire == 2:
            length, i = read_varint(buf, i)
            val, i = buf[i:i + length], i + length
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        fields.setdefault(field, []).append(val)
    return fields


def encode_canvas_request(entity_uris: list[str]) -> bytes:
    return b"".join(_len_field(1, _len_field(1, uri.encode())) for uri in entity_uris)


@dataclass
class Canvas:
    entity_uri: str
    url: str
    type: str
    id: str = ""
    file_id: str = ""
    canvas_uri: str = ""
    artist_name: str = ""
    artist_uri: str = ""

    @property
    def is_video(self) -> bool:
        return self.type != "IMAGE"


def _str(fields: dict[int, list], n: int) -> str:
    vals = fields.get(n)
    return vals[0].decode("utf-8", "replace") if vals else ""


def decode_canvas_response(buf: bytes) -> list[Canvas]:
    canvases = []
    for raw in decode_message(buf).get(1, []):
        f = decode_message(raw)
        artist = decode_message(f[6][0]) if 6 in f else {}
        canvases.append(Canvas(
            entity_uri=_str(f, 5),
            url=_str(f, 2),
            type=CANVAS_TYPES.get(f.get(4, [0])[0], "UNKNOWN"),
            id=_str(f, 1),
            file_id=_str(f, 3),
            canvas_uri=_str(f, 11),
            artist_name=_str(artist, 2),
            artist_uri=_str(artist, 1),
        ))
    return canvases
