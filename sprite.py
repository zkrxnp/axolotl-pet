"""아루 스프라이트 - AI로 생성한 복셀 아홀로틀 정지 이미지 한 장을 기반으로 한다.

axolotl.png (마젠타 배경을 제거해 이미 잘라 둔 RGBA 이미지) 위에 코드로
움직임을 얹는다. 진짜 3D 렌더링이 아니라 2D 후처리 트릭이다.
  - 꼬리 파도: 열(column)마다 위상이 다른 세로 이동을 줘서 깃발처럼 출렁이게 한다.
    머리/몸통 쪽은 가중치 0, 꼬리 끝으로 갈수록 가중치가 커진다.
  - 깜빡임: 눈 영역을 몸통 색으로 덮고 얇은 감은 눈 선을 그린다.
  - 좌우 반전: 이미지를 그대로 뒤집는다 (별도 아트 필요 없음).

배경 제거는 이미 끝난 상태(axolotl.png)라고 가정하지만, 리사이즈 시 반투명
가장자리에 배경색이 스며 나오는 걸 막기 위해 premultiplied alpha 로 축소한다
(그냥 축소하면 알파가 낮은 가장자리 픽셀의 RGB 가 아직 배경색을 머금고 있어서,
축소 후 옅은 마젠타 테두리가 생길 수 있다).

tkinter 의 -transparentcolor 는 이진 투명이라, 마지막에 알파를 임계값으로
이진화하고 투명한 곳은 KEY 컬러로 채워서 내보낸다.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageOps

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_PATH = os.path.join(_HERE, "axolotl.png")

KEY = (255, 0, 254)
KEY_HEX = "#ff00fe"
ALPHA_CUT = 128

PHASES = 16
LOGICAL_W = 190          # 배율 1 기준 출력 너비 (높이는 원본 비율로 계산)

# axolotl.png(1248x697) 안에서 눈/입 위치. _find_landmarks() 로 자동 검출했다.
EYE_BOXES = ((120, 386, 171, 441), (384, 434, 437, 491))
MOUTH_XY = (258, 498)
BODY_FILL = (201, 221, 242)          # 눈을 덮을 때 쓰는 얼굴 바탕색
CLOSED_EYE = (40, 45, 70)

TAIL_AMP = {"calm": 22.0, "excited": 40.0}   # 꼬리 파도 진폭 (원본 픽셀 기준)
TAIL_FREQ = 1.15                              # 꼬리 길이에 걸치는 파동 주기 수

_src_img = Image.open(_SRC_PATH).convert("RGBA")
SRC_W, SRC_H = _src_img.size
_SRC = np.asarray(_src_img)

_disp_scale = 1.0
_out = (LOGICAL_W, round(LOGICAL_W * SRC_H / SRC_W))
_cache = {}

# set_scale() 이 채운다: 매 프레임 다시 계산하기엔 비싼, 배율에 딸린 값들.
# 원본(1248x697)을 축소하는 LANCZOS 리샘플은 여기서 배율이 바뀔 때 딱 한 번만 하고,
# 꼬리 파도/깜빡임 같은 프레임별 편집은 이미 작아진 _base 위에서 처리한다
# (그래야 프레임 하나 만드는 데 대형 이미지를 매번 다시 축소하지 않는다).
_base = None
_px_scale = 1.0
_eye_boxes_px = EYE_BOXES
_mouth_xy_px = MOUTH_XY


def set_scale(scale: float):
    """디스플레이 배율에 맞춰 출력 크기를 바꾼다 (창을 만들기 전에 호출)."""
    global _disp_scale, _out, _cache, _base, _px_scale, _eye_boxes_px, _mouth_xy_px
    _disp_scale = max(1.0, float(scale))
    _out = (round(LOGICAL_W * _disp_scale), round(LOGICAL_W * SRC_H / SRC_W * _disp_scale))
    _cache = {}
    _base = _resize_rgba(_SRC, _out)
    _px_scale = _out[0] / SRC_W
    _eye_boxes_px = tuple(tuple(round(v * _px_scale) for v in box) for box in EYE_BOXES)
    _mouth_xy_px = (MOUTH_XY[0] * _px_scale, MOUTH_XY[1] * _px_scale)


def size():
    """현재 배율에서의 출력 스프라이트 크기 (px)."""
    return _out


# --------------------------------------------------------------------------- 원본 해상도 편집

def _tail_wave(arr: np.ndarray, phase: float, amp: float) -> np.ndarray:
    """열마다 위상이 다른 세로 이동을 줘서 꼬리가 깃발처럼 출렁이게 한다.

    머리/몸통(왼쪽)은 가중치가 0이라 그대로고, 꼬리 끝(오른쪽)으로 갈수록
    많이 흔들린다. 정수 픽셀 이동이라 부드럽지는 않지만, 원본이 복셀 아트라
    각진 움직임이 오히려 스타일과 잘 맞는다.
    """
    h, w = arr.shape[:2]
    xs = np.arange(w)
    weight = np.clip((xs / w - 0.30) / 0.60, 0.0, 1.0) ** 1.3
    dy = np.round(amp * weight * np.sin(2 * np.pi * (phase - xs / w * TAIL_FREQ))).astype(int)

    out = np.zeros_like(arr)
    for x in range(w):
        s = int(dy[x])
        if s == 0:
            out[:, x] = arr[:, x]
        elif s > 0:
            out[s:, x] = arr[: h - s, x]
        else:
            out[: h + s, x] = arr[-s:, x]
    return out


def _apply_blink(arr: np.ndarray) -> np.ndarray:
    """눈 위치를 얼굴색으로 덮고 얇은 감은 눈 선을 그린다 (이미 출력 크기로 축소된 배열)."""
    pad = max(1, round(3 * _px_scale))
    lw = max(1, round(7 * _px_scale))
    im = Image.fromarray(arr, "RGBA")
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in _eye_boxes_px:
        d.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], fill=BODY_FILL + (255,))
        cy = (y0 + y1) // 2
        d.line([(x0 + pad + 2, cy), (x1 - pad - 2, cy)], fill=CLOSED_EYE + (255,), width=lw)
    return np.asarray(im)


def _resize_rgba(arr: np.ndarray, out_wh) -> np.ndarray:
    """반투명 가장자리에 배경색이 번지지 않도록 premultiplied alpha 로 축소한다."""
    f = arr.astype(np.float32)
    a = f[..., 3:4] / 255.0
    premult = np.clip(f[..., :3] * a, 0, 255).astype(np.uint8)

    pm_img = Image.fromarray(premult, "RGB").resize(out_wh, Image.LANCZOS)
    a_img = Image.fromarray(arr[..., 3], "L").resize(out_wh, Image.LANCZOS)

    pm_r = np.asarray(pm_img).astype(np.float32)
    a_r = np.asarray(a_img).astype(np.float32)
    a_safe = np.clip(a_r, 1.0, 255.0)[..., None]
    rgb_r = np.clip(pm_r * 255.0 / a_safe, 0, 255)
    return np.dstack([rgb_r, a_r]).astype(np.uint8)


def _flatten(arr: np.ndarray) -> Image.Image:
    """알파를 이진화하고 투명한 곳을 KEY 컬러로 채운다 (tkinter 컬러키 투명용)."""
    mask = arr[..., 3] >= ALPHA_CUT
    rgb = arr[..., :3].copy()
    rgb[~mask] = KEY
    return Image.fromarray(rgb, "RGB")


def _render(phase_index: int, mood: str, eye_open: bool) -> Image.Image:
    phase = phase_index / PHASES
    amp = TAIL_AMP["excited" if mood == "excited" else "calm"] * _px_scale
    arr = _tail_wave(_base, phase, amp)
    if not eye_open:
        arr = _apply_blink(arr)
    return _flatten(arr)


# --------------------------------------------------------------------------- 캐시

def frame(phase_index: int, mood: str = "calm", eye_open: bool = True, facing: str = "right"):
    """캐시된 스프라이트 프레임(RGB, 투명부는 KEY 컬러)."""
    phase_index %= PHASES
    key = (phase_index, mood, eye_open, facing)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    # 원본 방향 렌더는 "_base" 라는 별도 키에 저장한다. "left"/"right" 는 실제 요청받는
    # facing 값이기도 해서, 그걸 내부 저장용 키로 같이 쓰면 먼저 들어온 요청이 캐시를
    # 채워놓는 바람에 나중 요청이 미러링을 건너뛰고 그 값을 그대로 돌려주는 버그가 난다.
    base_key = (phase_index, mood, eye_open, "_base")
    base = _cache.get(base_key)
    if base is None:
        base = _render(phase_index, mood, eye_open)
        _cache[base_key] = base
    # axolotl.png 원본은 얼굴이 이미지 왼쪽을 보고 있다 (몸통이 오른쪽 위로 뻗어 있음).
    # 그래서 "오른쪽으로 이동 중"일 때는 원본을 뒤집어야 얼굴이 진행 방향을 본다.
    img = base if facing == "left" else ImageOps.mirror(base)
    _cache[key] = img
    return img


def prewarm():
    """자주 쓰는 상태를 미리 그려 초반 끊김을 없앤다."""
    for mood in ("calm", "excited"):
        for eye_open in (True, False):
            for i in range(PHASES):
                for facing in ("left", "right"):
                    frame(i, mood, eye_open, facing)


def snout(facing: str):
    """물방울이 나올 입 위치 (출력 해상도의 스프라이트 로컬 좌표)."""
    x, y = _mouth_xy_px
    # 원본은 입이 왼쪽에 있다 (frame() 의 좌우 반전 규칙과 맞춰야 한다).
    return (x if facing == "left" else _out[0] - x, y)


set_scale(1.0)      # set_scale() 을 안 부르고 바로 frame()/render() 를 써도 안전하도록


if __name__ == "__main__":
    import sys

    sc = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    set_scale(sc)
    print(f"scale {sc}  out={size()}  src={SRC_W}x{SRC_H}")

    ow, oh = size()
    sheet = Image.new("RGB", (ow * 4, oh * 2), KEY)
    for i in range(8):
        img = frame(i, "calm" if i < 4 else "excited", i % 4 != 2, "right" if i % 2 else "left")
        sheet.paste(img, ((i % 4) * ow, (i // 4) * oh))
    sheet.save("preview.png")
    print("wrote preview.png")
