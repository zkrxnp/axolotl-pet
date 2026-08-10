"""아루 - 바탕화면을 헤엄쳐 다니는 파란 아홀로틀 데스크톱 펫.

실행:  run.bat  (또는 pythonw axo.py)
종료:  아홀로틀을 마우스 오른쪽 클릭 -> 종료
"""

import ctypes
import json
import math
import os
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

from PIL import ImageTk

import sprite

NAME = "아루"
CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "axopet", "config.json")

TICK_MS = 40                                # 25 fps
SWIM_HZ = {"rest": 0.5, "travel": 1.05, "excited": 1.9}
SPEED = {"travel": 105.0, "focus": 55.0, "follow": 320.0}       # 논리 px/s
SIZES = (("작게", 0.72), ("보통", 1.0), ("크게", 1.35))

BUBBLE_STROKE = "#79c4ef"
TEXT_FG = "#1f3b5c"
TEXT_BG = "#ffffff"
TEXT_EDGE = "#2f5a8f"
PILL_BG = "#eef7fd"

LINES = [
    "오늘도 화이팅!",
    "물 한 잔 마시고 와~",
    "허리 좀 펴고 앉아볼까?",
    "25분만 딱 집중해보자",
    "나 심심해...",
    "눈 뻑뻑하지? 잠깐 쉬어",
    "뽀글뽀글~",
    "너 지금 잘하고 있어",
    "저장은 했어?",
    "5분만 쉬었다 하자",
    "심호흡 한 번!",
    "졸리면 스트레칭!",
]


# --------------------------------------------------------------------------- DPI

def _init_dpi():
    """Tk 를 만들기 전에 DPI 인식을 켜고 배율을 알아낸다.

    이걸 안 하면 Windows 가 창을 통째로 늘려버려서 스프라이트가 뭉개진다.
    인식을 켠 뒤에는 tkinter 좌표가 곧 실제 픽셀이므로 배율만큼 크게 그려야 한다.
    """
    if sys.platform != "win32":
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return 1.0
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):
        return 1.0


DPI = _init_dpi()


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


# --------------------------------------------------------------------------- 앱

class Axo:
    def __init__(self):
        cfg = self._load_config()

        self.root = tk.Tk()
        self.root.title(NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=sprite.KEY_HEX)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", sprite.KEY_HEX)

        self.canvas = tk.Canvas(self.root, bg=sprite.KEY_HEX, highlightthickness=0, bd=0)
        self.canvas.pack()

        self.mode = tk.StringVar(value=cfg.get("mode", "wander"))
        self.topmost = tk.BooleanVar(value=cfg.get("topmost", True))
        self.pet = tk.DoubleVar(value=cfg.get("pet_scale", 1.0))

        # 상태
        self.t0 = time.time()
        self.now = 0.0
        self.phase = 0.0
        self.facing = "left"
        self.activity = "rest"                  # rest | travel
        self.hold_until = 0.0
        self.mood_until = 0.0
        self.blink_until = 0.0
        self.next_blink = 2.0
        self.next_bubble = 1.5
        self.bubbles = []
        self.speech = None
        self.speech_until = 0.0
        self.focus_end = None
        self.dragging = False
        self.drag_off = (0, 0)
        self.press = None
        self.img_id = None
        self._photos = {}

        self._fonts()
        self._layout()
        self.x = float(_clamp(cfg.get("x", self.x_max * 0.62), self.x_min, self.x_max))
        self.y = float(_clamp(cfg.get("y", self.y_max * 0.55), self.y_min, self.y_max))
        self.tx, self.ty = self.x, self.y

        self._build_menu()
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_menu)
        self.root.bind("<Escape>", lambda e: self.quit())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self._apply_geometry()
        self.say(f"안녕! 나는 {NAME}야", 3.4)
        self.root.after(TICK_MS, self.tick)

    # ----------------------------------------------------------------- 레이아웃

    def _fonts(self):
        fam = "Malgun Gothic" if "Malgun Gothic" in tkfont.families() else "TkDefaultFont"
        # 음수 크기는 포인트가 아니라 픽셀. 글자는 펫 크기와 무관하게 화면 배율만 따른다.
        self.font = tkfont.Font(family=fam, size=-round(13 * DPI))
        self.small = tkfont.Font(family=fam, size=-round(11 * DPI))

    def _layout(self):
        """펫 배율이 바뀔 때마다 스프라이트와 창 크기를 다시 잡는다."""
        self.u = DPI * self.pet.get()                   # 펫 기준 1 논리 px = 실제 몇 px
        sprite.set_scale(self.u)
        self.spr_w, self.spr_h = sprite.size()
        self.spr_x, self.spr_y = self.p(10), self.p(105)
        self.win_w = self.spr_w + self.p(20)
        self.win_h = self.spr_y + self.spr_h + self.p(6)

        self.canvas.configure(width=self.win_w, height=self.win_h)
        self._photos.clear()
        self.img_id = None
        self.canvas.delete("all")

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pad, taskbar = round(8 * DPI), round(56 * DPI)
        self.x_min, self.x_max = pad, max(pad, sw - self.win_w - pad)
        self.y_min, self.y_max = pad, max(pad, sh - self.win_h - taskbar)
        sprite.prewarm()

    def p(self, v):
        """펫 기준 논리 길이 -> 실제 픽셀."""
        return round(v * self.u)

    def d(self, v):
        """UI(말풍선 등) 기준 논리 길이 -> 실제 픽셀."""
        return round(v * DPI)

    def set_size(self):
        self.bubbles.clear()
        self._layout()
        self.x = float(_clamp(self.x, self.x_min, self.x_max))
        self.y = float(_clamp(self.y, self.y_min, self.y_max))
        self.tx, self.ty = self.x, self.y
        self._apply_geometry()

    # ----------------------------------------------------------------- 설정 저장

    def _load_config(self):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump({"x": round(self.x), "y": round(self.y),
                           "mode": self.mode.get(), "topmost": self.topmost.get(),
                           "pet_scale": self.pet.get()}, f)
        except OSError:
            pass

    # ----------------------------------------------------------------- 메뉴

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0, font=self.font)
        m.add_command(label="놀아주기", command=self.react)
        m.add_separator()
        m.add_radiobutton(label="자유롭게 헤엄치기", variable=self.mode, value="wander")
        m.add_radiobutton(label="마우스 따라다니기", variable=self.mode, value="follow")
        m.add_radiobutton(label="가만히 있기", variable=self.mode, value="stay")
        m.add_separator()
        m.add_command(label="집중 타이머 25분", command=self.toggle_focus)
        self.focus_item = m.index("end")
        m.add_separator()

        sizes = tk.Menu(m, tearoff=0, font=self.font)
        for label, value in SIZES:
            sizes.add_radiobutton(label=label, variable=self.pet, value=value,
                                  command=self.set_size)
        m.add_cascade(label="크기", menu=sizes)
        m.add_checkbutton(label="항상 맨 위", variable=self.topmost, command=self._apply_topmost)
        m.add_command(label="오른쪽 아래로 보내기", command=self.send_corner)
        m.add_separator()
        m.add_command(label="종료", command=self.quit)
        self.menu = m

    def on_menu(self, ev):
        if self.focus_end:
            left = max(0, int(self.focus_end - time.time()))
            self.menu.entryconfigure(self.focus_item,
                                     label=f"집중 타이머 중지 ({left // 60}:{left % 60:02d} 남음)")
        else:
            self.menu.entryconfigure(self.focus_item, label="집중 타이머 25분")
        try:
            self.menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.menu.grab_release()

    def _apply_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost.get()))

    def send_corner(self):
        self.tx, self.ty = self.x_max, self.y_max
        self.activity = "travel"
        self.hold_until = 0.0

    # ----------------------------------------------------------------- 동작

    def react(self):
        self.mood_until = self.now + 2.4
        self.say(random.choice(LINES), 3.0)
        for _ in range(7):
            self._spawn_bubble(burst=True)

    def say(self, text, secs=3.0):
        self.speech = text
        self.speech_until = self.now + secs

    def toggle_focus(self):
        if self.focus_end:
            self.focus_end = None
            self.say("집중 타이머 껐어", 2.6)
        else:
            self.focus_end = time.time() + 25 * 60
            self.say("25분 집중 시작!\n조용히 헤엄칠게", 4.0)

    def _finish_focus(self):
        self.focus_end = None
        self.mood_until = self.now + 4.0
        self.say("25분 완료! 정말 잘했어", 6.0)
        for _ in range(16):
            self._spawn_bubble(burst=True)

    # ----------------------------------------------------------------- 마우스

    def on_press(self, ev):
        self.press = (ev.x_root, ev.y_root, self.now)
        self.drag_off = (ev.x_root - int(self.x), ev.y_root - int(self.y))
        self.dragging = False

    def on_drag(self, ev):
        if not self.press:
            return
        if not self.dragging:
            if abs(ev.x_root - self.press[0]) + abs(ev.y_root - self.press[1]) < self.d(4):
                return
            self.dragging = True
        # 크기는 절대 건드리지 않는다 (드래그하면 점점 커지는 버그 방지)
        self.x = _clamp(ev.x_root - self.drag_off[0], self.x_min, self.x_max)
        self.y = _clamp(ev.y_root - self.drag_off[1], self.y_min, self.y_max)
        self.tx, self.ty = self.x, self.y
        self._apply_geometry()

    def on_release(self, ev):
        was_click = self.press and not self.dragging and (self.now - self.press[2]) < 0.45
        self.press = None
        if self.dragging:
            self.dragging = False
            self.activity = "rest"
            self.hold_until = self.now + 1.2
            self.say(random.choice(["어지러워~", "여기가 좋아?", "휴, 착지!"]), 2.2)
        elif was_click:
            self.react()

    # ----------------------------------------------------------------- 이동

    def _pick_target(self):
        span = 0.35 if self.focus_end else 1.0
        self.tx = _clamp(self.x + random.uniform(-1, 1) * (self.x_max - self.x_min) * 0.45 * span,
                         self.x_min, self.x_max)
        self.ty = _clamp(self.y + random.uniform(-1, 1) * (self.y_max - self.y_min) * 0.30 * span,
                         self.y_min, self.y_max)

    def _move(self, dt):
        if self.dragging:
            return

        if self.mode.get() == "follow":
            self.tx = _clamp(self.root.winfo_pointerx() - self.win_w // 2,
                             self.x_min, self.x_max)
            self.ty = _clamp(self.root.winfo_pointery() - (self.spr_y + self.spr_h // 2),
                             self.y_min, self.y_max)
            self.activity = "travel"
            speed = SPEED["follow"]
        elif self.mode.get() == "stay":
            self.activity = "rest"
            return
        else:
            if self.activity == "rest" and self.now >= self.hold_until:
                self._pick_target()
                self.activity = "travel"
            speed = SPEED["focus"] if self.focus_end else SPEED["travel"]

        dx, dy = self.tx - self.x, self.ty - self.y
        dist = math.hypot(dx, dy)
        if dist < self.d(3):
            # activity 가 이미 "rest" 면 도착한 지 오래된 것이므로 건드리지 않는다.
            # 매 틱 여기 걸릴 때마다 hold_until 을 다시 굴리면, 쉬는 시간이 끝나기도
            # 전에 계속 새 값으로 리셋돼서 절대 다시 못 움직이는 버그가 생긴다.
            if self.mode.get() != "follow" and self.activity != "rest":
                self.activity = "rest"
                lo, hi = (3.0, 9.0) if self.focus_end else (1.2, 4.5)
                self.hold_until = self.now + random.uniform(lo, hi)
            return

        step = min(dist, speed * DPI * dt * min(1.0, 0.25 + dist / (160.0 * DPI)))
        self.x += dx / dist * step
        self.y += dy / dist * step
        if abs(dx) > self.d(8):
            self.facing = "right" if dx > 0 else "left"

    def _apply_geometry(self, bob=0.0):
        self.root.geometry(f"{self.win_w}x{self.win_h}"
                           f"+{int(round(self.x))}+{int(round(self.y + bob))}")

    # ----------------------------------------------------------------- 물방울

    def _spawn_bubble(self, burst=False):
        sx, sy = sprite.snout(self.facing)
        u = self.u
        self.bubbles.append({
            "x": self.spr_x + sx + random.uniform(-5, 5) * u,
            "y": self.spr_y + sy + random.uniform(-4, 4) * u,
            "r": random.uniform(2.6, 6.4) * u * (1.15 if burst else 1.0),
            "vy": random.uniform(26, 46) * u * (1.4 if burst else 1.0),
            "drift": random.uniform(7, 18) * u,
            "ph": random.uniform(0, 6.28),
        })

    def _update_bubbles(self, dt):
        for b in self.bubbles:
            b["y"] -= b["vy"] * dt
            b["ph"] += dt * 2.4
            b["x"] += math.sin(b["ph"]) * b["drift"] * dt
            b["r"] += dt * 0.9 * self.u
        self.bubbles = [b for b in self.bubbles
                        if b["y"] > self.d(4) and 0 < b["x"] < self.win_w]

        if self.now >= self.next_bubble:
            gap = random.uniform(2.4, 5.0) if self.focus_end else random.uniform(0.8, 2.2)
            self.next_bubble = self.now + gap
            self._spawn_bubble()

    # ----------------------------------------------------------------- 그리기

    def _photo(self, key):
        img = self._photos.get(key)
        if img is None:
            img = ImageTk.PhotoImage(sprite.frame(*key))
            self._photos[key] = img
        return img

    @staticmethod
    def _round_rect(x0, y0, x1, y1, r):
        pts = []
        for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                           (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
            for i in range(6):
                a = math.radians(a0 + i * 18)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return pts

    def _draw_speech(self):
        lines = self.speech.split("\n")
        tw = max(self.font.measure(ln) for ln in lines)
        th = self.font.metrics("linespace") * len(lines)
        pad, edge = self.d(11), self.d(2)
        w, h = tw + pad * 2, th + pad * 2
        cx = self.win_w / 2
        x0, x1 = cx - w / 2, cx + w / 2
        y1 = self.spr_y - self.d(10)
        y0 = y1 - h
        if y0 < self.d(4):
            y0, y1 = self.d(4), self.d(4) + h

        self.canvas.create_polygon(self._round_rect(x0, y0, x1, y1, self.d(12)),
                                   smooth=True, splinesteps=12,
                                   fill=TEXT_BG, outline=TEXT_EDGE, width=edge, tags="ui")
        # 꼬리: 테두리째 그린 뒤 윗변을 말풍선 색으로 덮어 이음매를 지운다
        self.canvas.create_polygon(cx - self.d(9), y1 - 1, cx + self.d(9), y1 - 1,
                                   cx - self.d(2), y1 + self.d(11),
                                   fill=TEXT_BG, outline=TEXT_EDGE, width=edge, tags="ui")
        self.canvas.create_polygon(cx - self.d(7), y1 - edge - 1, cx + self.d(7), y1 - edge - 1,
                                   cx - self.d(2), y1 + self.d(8),
                                   fill=TEXT_BG, outline=TEXT_BG, tags="ui")
        self.canvas.create_text(cx, (y0 + y1) / 2, text=self.speech, font=self.font,
                                fill=TEXT_FG, justify="center", tags="ui")

    def _draw_focus_pill(self):
        left = max(0, int(self.focus_end - time.time()))
        label = f"집중 {left // 60}:{left % 60:02d}"
        w = self.small.measure(label) + self.d(20)
        h = self.small.metrics("linespace") + self.d(9)
        cx, y0 = self.win_w / 2, self.spr_y - self.d(30)
        self.canvas.create_polygon(self._round_rect(cx - w / 2, y0, cx + w / 2, y0 + h, h / 2),
                                   smooth=True, splinesteps=10, fill=PILL_BG,
                                   outline=TEXT_EDGE, width=self.d(2), tags="ui")
        self.canvas.create_text(cx, y0 + h / 2, text=label, font=self.small,
                                fill=TEXT_FG, tags="ui")

    def _draw(self):
        self.canvas.delete("ui")

        for b in self.bubbles:
            self.canvas.create_oval(b["x"] - b["r"], b["y"] - b["r"],
                                    b["x"] + b["r"], b["y"] + b["r"],
                                    outline=BUBBLE_STROKE, width=self.d(2), tags="ui")

        key = (int(self.phase * sprite.PHASES) % sprite.PHASES,
               "excited" if self.now < self.mood_until else "calm",
               self.now >= self.blink_until, self.facing)
        photo = self._photo(key)
        if self.img_id is None:
            self.img_id = self.canvas.create_image(self.spr_x, self.spr_y,
                                                   image=photo, anchor="nw")
        else:
            self.canvas.itemconfigure(self.img_id, image=photo)

        if self.speech and self.now < self.speech_until:
            self._draw_speech()
        else:
            self.speech = None
            if self.focus_end:
                self._draw_focus_pill()

    # ----------------------------------------------------------------- 루프

    def tick(self):
        prev = self.now
        self.now = time.time() - self.t0
        dt = min(0.12, self.now - prev)

        if self.focus_end and time.time() >= self.focus_end:
            self._finish_focus()

        state = ("excited" if self.now < self.mood_until
                 else ("travel" if self.activity == "travel" else "rest"))
        self.phase = (self.phase + SWIM_HZ[state] * dt) % 1.0

        if self.now >= self.next_blink:
            self.blink_until = self.now + 0.11
            self.next_blink = self.now + random.uniform(2.4, 6.5)

        self._move(dt)
        self._update_bubbles(dt)

        if not self.dragging:
            amp = self.p(3.6) if self.activity == "rest" else self.p(1.6)
            self._apply_geometry(amp * math.sin(self.now * 2.0))

        self._draw()
        self.root.after(TICK_MS, self.tick)

    def quit(self):
        self._save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Axo().run()
