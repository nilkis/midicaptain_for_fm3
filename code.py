import time
import json
import board
import keypad
import neopixel
import busio
import displayio
import terminalio
import digitalio
import rotaryio
import adafruit_midi
from adafruit_midi.system_exclusive import SystemExclusive
from adafruit_midi.control_change import ControlChange
from adafruit_st7789 import ST7789
from adafruit_display_text import label
import gc

# ============================================================
# FM3 SysEx Protocol Constants
# ============================================================
FRACTAL_MFR_ID = (0x00, 0x01, 0x74)
FM3_MODEL_ID = 0x11

SET_FX_STATUS = 0x0A
SET_CHANNEL = 0x0B
SET_SCENE = 0x0C
QUERY_PATCH_NAME = 0x0D
QUERY_SCENE_NAME = 0x0E
LOOPER_FUNC = 0x0F
TAP_TEMPO_FUNC = 0x10
TUNER_FUNC = 0x11
STATUS_DUMP = 0x13
SET_GET_TEMPO = 0x14

# Looper (SysEx 0x0F) — 버튼 값 0-5, 상태는 비트마스크로 수신
# state bits: 0=Record 1=Play 2=Overdub 3=Once 4=Reverse 5=Half-speed
LOOPER_BTN_NAMES = ("REC", "PLAY", "UNDO", "ONCE", "REV", "HALF")
LOOPER_BIT_REC, LOOPER_BIT_PLAY, LOOPER_BIT_OVERDUB = 0x01, 0x02, 0x04
LOOPER_BIT_ONCE, LOOPER_BIT_REV, LOOPER_BIT_HALF = 0x08, 0x10, 0x20
# 버튼별 상태 마스크 (REC은 별도 2색 처리, UNDO는 tap 타입)
LOOPER_LED_MASKS = (LOOPER_BIT_REC | LOOPER_BIT_OVERDUB, LOOPER_BIT_PLAY, 0,
                    LOOPER_BIT_ONCE, LOOPER_BIT_REV, LOOPER_BIT_HALF)

# Effect IDs (Fractal SysEx spec Appendix)
EFFECT_IDS = {
    "COMP1": 46, "COMP2": 47,
    "GRAPHEQ1": 50, "GRAPHEQ2": 51,
    "PARAEQ1": 54, "PARAEQ2": 55,
    "AMP1": 58,  # SysEx ID_DISTORT1. FM3는 Amp 1개
    "REVERB1": 66,
    "DELAY1": 70, "DELAY2": 71,
    "MULTITAP1": 74,
    "CHORUS1": 78, "CHORUS2": 79,
    "FLANGER1": 82, "FLANGER2": 83,
    "ROTARY1": 86, "ROTARY2": 87,
    "PHASER1": 90, "PHASER2": 91,
    "WAH1": 94, "WAH2": 95,
    "FORMANT1": 98, "FORMANT2": 99,
    "VOLUME1": 102, "VOLUME2": 103,
    "TREMOLO1": 106, "TREMOLO2": 107,
    "PITCH1": 110,
    "FILTER1": 114, "FILTER2": 115, "FILTER3": 116, "FILTER4": 117,
    "DRIVE1": 118, "DRIVE2": 119,
    "ENHANCER1": 122, "ENHANCER2": 123,
    "MIXER1": 126, "MIXER2": 127, "MIXER3": 128, "MIXER4": 129,
    "SYNTH1": 130,
    "MEGATAP1": 138,
    "GATE1": 146, "GATE2": 147,
    "RINGMOD1": 150,
    "MULTICOMP1": 154,
    "TENTAP1": 158,
    "RESONATOR1": 162, "RESONATOR2": 163,
    "LOOPER1": 166,
    "PLEX1": 178,
    "FBSEND1": 182, "FBSEND2": 183,
    "FBRETURN1": 186, "FBRETURN2": 187,
    "MIDIBLOCK": 190,
    "MULTIPLEXER1": 191, "MULTIPLEXER2": 192,
}

EFFECT_NAMES = {v: k for k, v in EFFECT_IDS.items()}

# ============================================================
# Color Palette — FM3 / FC 컨트롤러 12색 (doc/design_mod.org)
# ============================================================
PALETTE_NAMES = ("RED", "ORANGE", "YELLOW", "YELLOW_GREEN", "GREEN", "TEAL",
                 "CYAN", "BLUE", "PURPLE", "PINK", "WHITE", "OFF")
FRACTAL_COLORS = {
    "RED":          (255, 0, 0),
    "ORANGE":       (255, 100, 0),
    "YELLOW":       (255, 220, 0),
    "YELLOW_GREEN": (150, 255, 0),
    "GREEN":        (0, 255, 0),
    "TEAL":         (0, 200, 120),
    "CYAN":         (0, 220, 220),
    "BLUE":         (0, 80, 255),
    "PURPLE":       (150, 0, 255),
    "PINK":         (255, 0, 150),
    "WHITE":        (200, 200, 200),
    "OFF":          (0, 0, 0),
}
# 화면 표시용 짧은 이름
PALETTE_ABBREV = {
    "RED": "Red", "ORANGE": "Orng", "YELLOW": "Yel", "YELLOW_GREEN": "YlGr",
    "GREEN": "Grn", "TEAL": "Teal", "CYAN": "Cyan", "BLUE": "Blue",
    "PURPLE": "Purp", "PINK": "Pink", "WHITE": "Whit", "OFF": "Off",
}


def pal(name):
    """팔레트 이름 → RGB tuple. 미지정/오타는 OFF."""
    return FRACTAL_COLORS.get(name, (0, 0, 0))


# ============================================================
# Hardware Configuration
# ============================================================
BUTTON_PINS = (
    board.GP1, board.GP25, board.GP24, board.GP23, board.GP20,
    board.GP9, board.GP10, board.GP11, board.GP18, board.GP19,
)
BUTTON_NAMES = (
    'switch1', 'switch2', 'switch3', 'switch4', 'switchUp',
    'switchA', 'switchB', 'switchC', 'switchD', 'switchDown',
)
NUM_BUTTONS = 10

# Display SPI pins
DISPLAY_CLK = board.GP14
DISPLAY_MOSI = board.GP15
DISPLAY_DC = board.GP12
DISPLAY_CS = board.GP13
DISPLAY_BL = board.GP8

NEOPIXEL_PIN = board.GP7
NUM_PIXELS = 30
LEDS_PER_BUTTON = 3

# Rotary encoder pins
ENCODER_A = board.GP2
ENCODER_B = board.GP3
ENCODER_SW = board.GP0

OFF_W = 0.02  # deactivation 밝기 비율 (설계 확정값)
TAP_FLASH_SEC = 0.15  # tap 타입 순간 점등 시간

HOLD_TIME_DEFAULT = 0.5
HOLD_TIME_MIN, HOLD_TIME_MAX = 0.3, 2.0
# Hold 발동 시점 (전역): "release" = 발을 뗄 때, "timeout" = Hold Time 경과 즉시
HOLD_MODES = ("release", "timeout")
HOLD_MODE_DEFAULT = "release"

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# ============================================================
# Config v3 — 팔레트 이름 저장, 페이지 8개, 전역 hold_time
# ============================================================
# 액션 스키마 (dict):
#   type: none | scene | preset_inc | preset_dec | effect | channel_select
#         | tuner | tap_tempo | looper | page_inc | page_dec
#   scene:          number(1-8)
#   effect:         effect(name), color(pal|list of 3 pal),
#                   rotation(bool, hold sub-function), channels([0-3]),
#                   ch_colors([4 x (pal | [pal,pal,pal])])  — 채널별 단색 또는 per-LED
#   channel_select: effect(name), channel(0-3), color
#   looper:         button(0-5), color, color2 (REC overdub 전용)
#   나머지:         color
CONFIG_VERSION = 3
CONFIG_FILE = "config.json"


def _a(atype, color="OFF", **kw):
    d = {"type": atype, "color": color}
    d.update(kw)
    return d


def _none():
    return {"type": "none", "color": "OFF"}


def _btn(press, hold=None):
    return {"press": press, "hold": hold if hold is not None else _none()}


def _fx(name, color, rotation=False, ch_colors=None):
    d = _a("effect", color, effect=name)
    if rotation:
        d["rotation"] = True
        d["channels"] = [0, 1, 2, 3]
        d["ch_colors"] = list(ch_colors) if ch_colors else ["GREEN", "YELLOW", "ORANGE", "RED"]
    return d


def _chsel(name, ch, color):
    return _a("channel_select", color, effect=name, channel=ch)


def _looper(btn, color, color2=None):
    d = _a("looper", color, button=btn)
    if color2:
        d["color2"] = color2
    return d


def _scene(n, color="RED"):
    return _a("scene", color, number=n)


def _nav_updn():
    """Up/Dn 공통: preset ± / page ±"""
    return [_btn(_a("preset_inc", "GREEN"), _a("page_inc", "WHITE")),
            _btn(_a("preset_dec", "GREEN"), _a("page_dec", "WHITE"))]


def _blank_page(name):
    return {"name": name, "buttons": [_btn(_none()) for _ in range(NUM_BUTTONS)]}


def _tap_tuner():
    return _btn(_a("tap_tempo", "BLUE"), _a("tuner", "BLUE"))


def default_config():
    """호출 시마다 새 객체 (공유 참조 방지 — 페이지 간에도 버튼 dict를 공유하지 않는다).
    doc/design_mod.org Pre-defined Page."""
    # Page1: Scene
    up, dn = _nav_updn()
    p1 = [_btn(_scene(1)), _btn(_scene(2)), _btn(_scene(3)), _btn(_scene(4)), up,
          _btn(_scene(5)), _btn(_scene(6)), _btn(_scene(7)), _btn(_scene(8)), dn]

    # Page2: Effects
    up, dn = _nav_updn()
    p2 = [
        _btn(_fx("CHORUS1", ["CYAN", "BLUE", "CYAN"])),
        _btn(_fx("PHASER1", "GREEN")),
        _btn(_fx("FLANGER1", "PINK")),
        _tap_tuner(),
        up,
        _btn(_fx("COMP1", "CYAN")),
        _btn(_fx("DRIVE1", "ORANGE", rotation=True,
                 ch_colors=["YELLOW", "YELLOW_GREEN", "ORANGE", "RED"])),
        _btn(_fx("PARAEQ1", "PURPLE")),
        _btn(_fx("DELAY1", "BLUE", rotation=True,
                 ch_colors=["CYAN", "TEAL", "BLUE", "PURPLE"])),
        dn,
    ]

    # Page3: Amp Channel
    up, dn = _nav_updn()
    p3 = [
        _btn(_fx("COMP1", "CYAN")),
        _btn(_fx("DRIVE1", "ORANGE", rotation=True,
                 ch_colors=["YELLOW", "YELLOW_GREEN", "ORANGE", "RED"])),
        _btn(_fx("PARAEQ1", "PURPLE")),
        _btn(_fx("DELAY1", "BLUE", rotation=True,
                 ch_colors=["CYAN", "TEAL", "BLUE", "PURPLE"])),
        up,
        _btn(_chsel("AMP1", 0, "GREEN")),
        _btn(_chsel("AMP1", 1, "YELLOW")),
        _btn(_chsel("AMP1", 2, "ORANGE")),
        _btn(_chsel("AMP1", 3, "RED")),
        dn,
    ]

    # Page4: Looper
    up, dn = _nav_updn()
    p4 = [
        _btn(_looper(4, "BLUE")),                 # REV
        _btn(_looper(5, "PURPLE")),               # HALF
        _btn(_fx("LOOPER1", "PINK")),             # looper 블록 on/off
        _tap_tuner(),
        up,
        _btn(_looper(1, "GREEN")),                # PLAY
        _btn(_looper(0, "RED", color2="ORANGE")), # REC (rec/overdub)
        _btn(_looper(2, "WHITE")),                # UNDO
        _btn(_looper(3, "CYAN")),                 # ONCE
        dn,
    ]

    pages = [
        {"name": "SCENE", "buttons": p1},
        {"name": "FX", "buttons": p2},
        {"name": "AMP", "buttons": p3},
        {"name": "LOOPER", "buttons": p4},
    ]
    for i in range(1, 5):
        pg = _blank_page("USER%d" % i)
        # User 페이지에도 Up/Dn 내비게이션은 기본 제공
        u, d = _nav_updn()
        pg["buttons"][4] = u
        pg["buttons"][9] = d
        pages.append(pg)

    return {"version": CONFIG_VERSION, "hold_time": HOLD_TIME_DEFAULT,
            "hold_mode": HOLD_MODE_DEFAULT, "last_page": 0,
            "start_page": -1,  # -1 = Last(마지막 페이지 복원), 0~7 = 고정 페이지
            "pages": pages}


def _copy_json(obj):
    return json.loads(json.dumps(obj))


def load_config():
    """v3만 로드. 그 외(v1/v2/손상)는 신규 기본값으로 overwrite (설계 결정)."""
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        if (isinstance(cfg, dict) and cfg.get("version") == CONFIG_VERSION
                and isinstance(cfg.get("pages"), list) and cfg["pages"]
                and all(isinstance(p, dict) and isinstance(p.get("buttons"), list)
                        and len(p["buttons"]) == NUM_BUTTONS for p in cfg["pages"])):
            cfg.setdefault("hold_time", HOLD_TIME_DEFAULT)
            cfg.setdefault("hold_mode", HOLD_MODE_DEFAULT)
            cfg.setdefault("last_page", 0)
            cfg.setdefault("start_page", -1)
            return cfg
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    cfg = default_config()
    save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError as e:
        print("Config save error:", e)


def color_off(c):
    return (int(c[0] * OFF_W), int(c[1] * OFF_W), int(c[2] * OFF_W))


def color_leds(spec):
    """색 지정값 → LED 3개 RGB 리스트. 문자열(팔레트명)이면 3개 동일, 리스트면 per-LED."""
    if isinstance(spec, list):
        out = [pal(x) for x in spec[:3]]
        while len(out) < 3:
            out.append(out[-1] if out else (0, 0, 0))
        return out
    rgb = pal(spec)
    return [rgb, rgb, rgb]


def action_colors(action):
    """action의 color 필드 → LED 3개 RGB 리스트."""
    return color_leds(action.get("color", "OFF"))


# ============================================================
# Tap Tempo
# ============================================================
class TapTempo:
    def __init__(self, flash_duration=0.08):
        self.flash_duration = flash_duration
        self.last_tap = 0
        self.interval = 0.5
        self._beat_start = 0

    def on_tap(self):
        now = time.monotonic()
        if self.last_tap > 0:
            gap = now - self.last_tap
            if 0.2 < gap < 2.0:
                self.interval = gap
        self.last_tap = now
        self._beat_start = now

    def is_flashing(self):
        if self.interval <= 0:
            return False
        now = time.monotonic()
        elapsed = now - self._beat_start
        beat_phase = (elapsed % self.interval) / self.interval
        flash_ratio = self.flash_duration / self.interval
        return beat_phase < flash_ratio


# ============================================================
# LED Manager
# ============================================================
class LEDManager:
    def __init__(self, pin, num_pixels):
        self.pixels = neopixel.NeoPixel(pin, num_pixels, brightness=0.5, auto_write=False)
        self.num_pixels = num_pixels
        self.num_buttons = num_pixels // LEDS_PER_BUTTON
        self.button_leds = [[(0, 0, 0)] * LEDS_PER_BUTTON for _ in range(self.num_buttons)]
        self.dirty = True

    def set_button_color(self, btn_idx, color):
        if 0 <= btn_idx < self.num_buttons:
            c = tuple(color)
            new = [c, c, c]
            if self.button_leds[btn_idx] != new:
                self.button_leds[btn_idx] = new
                self.dirty = True

    def set_button_leds(self, btn_idx, led1, led2, led3):
        if 0 <= btn_idx < self.num_buttons:
            new = [tuple(led1), tuple(led2), tuple(led3)]
            if self.button_leds[btn_idx] != new:
                self.button_leds[btn_idx] = new
                self.dirty = True

    def update(self):
        if not self.dirty:
            return
        self.dirty = False
        for btn in range(self.num_buttons):
            base = btn * LEDS_PER_BUTTON
            leds = self.button_leds[btn]
            if btn < 5:
                # group1 (삼각형): pixel 순서 = LED1, LED2, LED3
                self.pixels[base] = leds[0]
                self.pixels[base + 1] = leds[1]
                self.pixels[base + 2] = leds[2]
            else:
                # group2 (역삼각형): pixel 순서 = LED1, LED3, LED2
                self.pixels[base] = leds[0]
                self.pixels[base + 1] = leds[2]
                self.pixels[base + 2] = leds[1]
        self.pixels.show()


# ============================================================
# FM3 Controller
# ============================================================
class FM3Controller:
    # 액션 타입 분류
    TAP_TYPES = ("preset_inc", "preset_dec", "page_inc", "page_dec")
    STATE_TYPES = ("effect", "scene", "channel_select", "looper")  # LED가 FM3 상태를 반영

    def __init__(self):
        self.full_config = load_config()
        self.pages = self.full_config["pages"]
        # 시작 페이지: start_page가 유효하면 고정, -1(Last)이면 마지막 사용 페이지 복원
        n = len(self.pages)
        sp = self.full_config.get("start_page", -1)
        self.start_page = sp if isinstance(sp, int) and -1 <= sp < n else -1
        if self.start_page >= 0:
            self.page_idx = self.start_page
        else:
            lp = self.full_config.get("last_page", 0)
            self.page_idx = lp if isinstance(lp, int) and 0 <= lp < n else 0
        self.config = self.pages[self.page_idx]["buttons"]
        self.page_save_at = 0  # 페이지 전환 후 지연 저장 예약 시각 (flash 마모 방지)
        self.hold_time = self.full_config.get("hold_time", HOLD_TIME_DEFAULT)
        self.hold_mode = self.full_config.get("hold_mode", HOLD_MODE_DEFAULT)
        if self.hold_mode not in HOLD_MODES:
            self.hold_mode = HOLD_MODE_DEFAULT
        self.hold_fired = set()  # timeout 모드에서 이미 hold 발동한 버튼 (release 시 무시)

        # MIDI setup
        self.uart = busio.UART(
            tx=board.GP16, rx=board.GP17,
            baudrate=31250, timeout=0.001,
            receiver_buffer_size=2048,
        )
        self.midi = adafruit_midi.MIDI(
            midi_in=self.uart, midi_out=self.uart,
            out_channel=0, debug=False, in_buf_size=128
        )

        # Buttons
        self.keys = keypad.Keys(
            pins=BUTTON_PINS, value_when_pressed=False, pull=True, interval=0.02,
        )
        self.press_times = {}

        # Display
        displayio.release_displays()
        spi = busio.SPI(clock=DISPLAY_CLK, MOSI=DISPLAY_MOSI)
        # baudrate 미지정 시 CircuitPython 기본값이 느려 전체 화면 refresh에 ~350ms 소요됨.
        # ST7789는 수십 MHz까지 지원 → 24MHz로 refresh를 수십 ms대로 단축 (로터리 반응성)
        display_bus = displayio.FourWire(
            spi, command=DISPLAY_DC, chip_select=DISPLAY_CS, baudrate=24_000_000)
        self.display = ST7789(
            display_bus, width=240, height=240,
            rowstart=80, rotation=180, backlight_pin=DISPLAY_BL,
        )
        self.display.auto_refresh = False

        # LEDs
        self.leds = LEDManager(NEOPIXEL_PIN, NUM_PIXELS)
        self.tap = TapTempo()
        self.tap_flash_until = [0.0] * NUM_BUTTONS  # tap 타입 순간 점등 만료 시각

        # Rotary encoder
        self.encoder = rotaryio.IncrementalEncoder(ENCODER_A, ENCODER_B)
        self.encoder_sw = digitalio.DigitalInOut(ENCODER_SW)
        self.encoder_sw.direction = digitalio.Direction.INPUT
        self.encoder_sw.pull = digitalio.Pull.UP
        self.encoder_last_pos = self.encoder.position
        self.encoder_sw_pressed = False
        self.encoder_sw_press_time = 0

        # FM3 state
        self.fx_states = {}
        self.fx_channels = {}
        self.fx_num_channels = {}
        self.current_scene = None
        self.patch_name = ""
        self.patch_number = None
        self.scene_name = ""
        self.tempo_bpm = 120
        self.looper_state = 0
        self.name_query_at = 0
        self._dump_sig = None
        self.rx_buf = b""

        # Polling
        self.poll_timer = 0
        self.poll_interval = 0.15
        self.poll_count = 0

        # Display state
        self.display_dirty = True
        self.display_temp_name = None
        self.display_temp_state = ""
        self.display_temp_until = 0
        self.tuner_active = False
        self.tuner_note = 0
        self.tuner_string = 0
        self.tuner_cents = 0
        self.tuner_last_data = 0
        self.tuner_ignore_until = 0  # tuner off 직후 잔여 push 무시 시각

        # Edit mode
        self.edit_mode = False
        self.edit_screen = 0      # SCR_MAIN / SWITCH / ACTION / PARAM / GLOBAL
        self.edit_cursor = 0
        self.edit_page = 0        # Switch Setup 대상 페이지
        self.edit_btn_idx = 0
        self.edit_press_idx = 0   # 0=press, 1=hold
        self.edit_editing_value = False
        self.edit_led_idx = 3     # per-LED 색상 편집 시 LED 번호(0-2), 3=ALL
        self.edit_name = []       # 페이지 이름 편집 버퍼
        self.edit_name_pos = 0
        self.edit_ch_idx = 0      # Chans 편집 서브커서 (0-3)
        self.edit_redraw_at = 0   # 회전 디바운스: 이 시각 이후에 화면 갱신
        self.copy_dst = 0         # Copy Page 대상 페이지

        self._build_lookups()
        self._init_display_groups()

    # --------------------------------------------------------
    # Lookups
    # --------------------------------------------------------
    def _build_lookups(self):
        self.fx_to_btn = {}
        self.scene_buttons = []
        self.tap_tempo_buttons = []
        self.looper_buttons = []
        for i, cfg in enumerate(self.config):
            for key in ("press", "hold"):
                a = cfg.get(key, {})
                t = a.get("type", "none")
                if t in ("effect", "channel_select"):
                    fx_id = EFFECT_IDS.get(a.get("effect", ""))
                    if fx_id is not None:
                        self.fx_to_btn.setdefault(fx_id, [])
                        if i not in self.fx_to_btn[fx_id]:
                            self.fx_to_btn[fx_id].append(i)
                elif t == "scene":
                    if i not in self.scene_buttons:
                        self.scene_buttons.append(i)
                elif t == "tap_tempo":
                    if i not in self.tap_tempo_buttons:
                        self.tap_tempo_buttons.append(i)
                elif t == "looper":
                    if i not in self.looper_buttons:
                        self.looper_buttons.append(i)

    # --------------------------------------------------------
    # Page switching
    # --------------------------------------------------------
    def _change_page(self, delta):
        n = len(self.pages)
        if n <= 1:
            return
        self.page_idx = (self.page_idx + delta) % n
        page = self.pages[self.page_idx]
        self.config = page["buttons"]
        self._build_lookups()
        self._update_all_button_leds()
        if self.looper_buttons:
            self.send_get_looper()
        self._show_temp("PAGE %d" % (self.page_idx + 1), page.get("name", ""))
        # 3초간 추가 전환이 없으면 last_page 저장 (연속 회전 중 매번 쓰지 않음)
        self.page_save_at = time.monotonic() + 3.0

    def _save_last_page(self):
        if self.full_config.get("last_page") != self.page_idx:
            self.full_config["last_page"] = self.page_idx
            save_config(self.full_config)

    # --------------------------------------------------------
    # Display groups
    # --------------------------------------------------------
    def _init_display_groups(self):
        # --- Normal screen ---
        self.grp_normal = displayio.Group()
        bg = displayio.Bitmap(240, 240, 1)
        p = displayio.Palette(1)
        p[0] = 0x000000
        self.grp_normal.append(displayio.TileGrid(bg, pixel_shader=p))

        self.lbl_page = label.Label(
            terminalio.FONT, text="P1", color=0x888888, scale=2,
            anchor_point=(0.0, 0.0), anchored_position=(5, 5),
        )
        self.grp_normal.append(self.lbl_page)

        self.lbl_bpm = label.Label(
            terminalio.FONT, text="BPM:120", color=0x888888, scale=2,
            anchor_point=(1.0, 0.0), anchored_position=(235, 5),
        )
        self.grp_normal.append(self.lbl_bpm)

        sep_bmp = displayio.Bitmap(220, 1, 1)
        sep_pal = displayio.Palette(1)
        sep_pal[0] = 0x444444
        self.grp_normal.append(displayio.TileGrid(sep_bmp, pixel_shader=sep_pal, x=10, y=28))

        # Preset name — scale 3 (설계: 폰트 확대). terminalio 6px*3=18px/char → 13자
        self.lbl_patch = label.Label(
            terminalio.FONT, text="---", color=0xFFFFFF, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 36),
        )
        self.grp_normal.append(self.lbl_patch)

        # Scene name — scale 3
        self.lbl_scene = label.Label(
            terminalio.FONT, text="---", color=0x00AAFF, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 78),
        )
        self.grp_normal.append(self.lbl_scene)

        self.lbl_action_name = label.Label(
            terminalio.FONT, text="", color=0xFFFF00, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 130),
        )
        self.grp_normal.append(self.lbl_action_name)

        self.lbl_action_state = label.Label(
            terminalio.FONT, text="", color=0xFFFF00, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 178),
        )
        self.grp_normal.append(self.lbl_action_state)

        # --- Tuner screen ---
        self.grp_tuner = displayio.Group()
        bg3 = displayio.Bitmap(240, 240, 1)
        p3 = displayio.Palette(1)
        p3[0] = 0x000000
        self.grp_tuner.append(displayio.TileGrid(bg3, pixel_shader=p3))
        self.grp_tuner.append(label.Label(
            terminalio.FONT, text="TUNER", color=0xFFFFFF, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 20),
        ))
        self.lbl_tuner_info = label.Label(
            terminalio.FONT, text="Note: -  Str: -", color=0x00FFFF, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 90),
        )
        self.grp_tuner.append(self.lbl_tuner_info)
        self.lbl_tuner_cents = label.Label(
            terminalio.FONT, text="---", color=0xFFFF00, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 160),
        )
        self.grp_tuner.append(self.lbl_tuner_cents)

        # --- Edit screen ---
        self.grp_edit = displayio.Group()
        bg4 = displayio.Bitmap(240, 240, 1)
        p4 = displayio.Palette(1)
        p4[0] = 0x000000
        self.grp_edit.append(displayio.TileGrid(bg4, pixel_shader=p4))
        self.lbl_edit_title = label.Label(
            terminalio.FONT, text="[EDIT]", color=0xFFFF00, scale=2,
            anchor_point=(0.0, 0.0), anchored_position=(4, 3),
        )
        self.grp_edit.append(self.lbl_edit_title)
        # 테이블 행 13개 (scale 2: 12x14px 글자, 20자 폭, 행간 17px)
        self.EDIT_ROW_Y0 = 24
        self.EDIT_ROW_H = 17
        self.lbl_edit_rows = []
        for row in range(self.EDIT_ROWS):
            l = label.Label(
                terminalio.FONT, text="", color=0xAAAAAA, scale=2,
                anchor_point=(0.0, 0.0),
                anchored_position=(2, self.EDIT_ROW_Y0 + row * self.EDIT_ROW_H),
            )
            self.grp_edit.append(l)
            self.lbl_edit_rows.append(l)
        # 커서 표시 전용 라벨(">"/"*") 하나 — 행 텍스트에 커서를 넣지 않고 이 라벨만 이동.
        # (행별 라벨 13개는 no-dirty refresh 기본 비용을 12→55ms로 늘려 오히려 손해)
        self.lbl_edit_cursor = label.Label(
            terminalio.FONT, text=">", color=0x00FF00, scale=2,
            anchor_point=(0.0, 0.0), anchored_position=(2, self.EDIT_ROW_Y0),
        )
        self.lbl_edit_cursor.hidden = True
        self.grp_edit.append(self.lbl_edit_cursor)
        self._cursor_row = -1

        # 테이블 구분선 (Switch 테이블 전용, 기본 숨김)
        line_pal = displayio.Palette(1)
        line_pal[0] = 0x444444
        # 가로선: 헤더 아래
        self.tbl_hline = displayio.TileGrid(displayio.Bitmap(236, 1, 1), pixel_shader=line_pal, x=2, y=0)
        self.tbl_hline.hidden = True
        self.grp_edit.append(self.tbl_hline)
        # 세로선 2개: SW|Press, Press|Hold
        self.tbl_vlines = []
        for _ in range(2):
            v = displayio.TileGrid(displayio.Bitmap(1, 232, 1), pixel_shader=line_pal, x=0, y=0)
            v.hidden = True
            self.grp_edit.append(v)
            self.tbl_vlines.append(v)

        # 색상 견본 3개 (per-LED L1/L2/L3). 1x1 비트맵을 TileGrid로 확대 — 색 변경은
        # palette[0] 한 줄이라 부하 없음. 기본은 숨김.
        self.swatch_pals = []
        self.swatches = []
        self.SWATCH_SZ = 16
        # 편집 대상 LED 강조용 흰 테두리 (견본보다 2px 크게, 견본 뒤에 그려짐)
        frame_pal = displayio.Palette(1)
        frame_pal[0] = 0xFFFFFF
        self.swatch_frame = displayio.TileGrid(
            displayio.Bitmap(self.SWATCH_SZ + 4, self.SWATCH_SZ + 4, 1), pixel_shader=frame_pal, x=0, y=0)
        self.swatch_frame.hidden = True
        self.grp_edit.append(self.swatch_frame)
        for i in range(3):
            bmp = displayio.Bitmap(self.SWATCH_SZ, self.SWATCH_SZ, 1)
            sp = displayio.Palette(1)
            sp[0] = 0x000000
            tg = displayio.TileGrid(bmp, pixel_shader=sp, x=0, y=0)
            tg.hidden = True
            self.grp_edit.append(tg)
            self.swatch_pals.append(sp)
            self.swatches.append(tg)

        self._current_group = None

    def _show_group(self, grp):
        if self._current_group is not grp:
            self.display.show(grp)
            self._current_group = grp

    # --------------------------------------------------------
    # SysEx send
    # --------------------------------------------------------
    def _calc_checksum(self, mid, data):
        checksum = 0xF0
        for b in mid:
            checksum ^= b
        for b in data:
            checksum ^= b
        return checksum & 0x7F

    def _send_sysex(self, data):
        data.append(self._calc_checksum(FRACTAL_MFR_ID, data))
        self.midi.send(SystemExclusive(FRACTAL_MFR_ID, data))

    def send_status_dump(self):
        self._send_sysex([FM3_MODEL_ID, STATUS_DUMP])

    def send_tap_tempo(self):
        self._send_sysex([FM3_MODEL_ID, TAP_TEMPO_FUNC])
        self.tap.on_tap()
        self.send_get_tempo()

    def send_tuner(self, on):
        self._send_sysex([FM3_MODEL_ID, TUNER_FUNC, 1 if on else 0])

    def send_set_scene(self, scene_num):
        self._send_sysex([FM3_MODEL_ID, SET_SCENE, scene_num])

    def send_get_scene(self):
        self._send_sysex([FM3_MODEL_ID, SET_SCENE, 0x7F])

    def send_set_fx_status(self, fx_id, value):
        self._send_sysex([FM3_MODEL_ID, SET_FX_STATUS, int(fx_id) % 128, int(fx_id) // 128, value])

    def send_set_channel(self, fx_id, channel):
        self._send_sysex([FM3_MODEL_ID, SET_CHANNEL, int(fx_id) % 128, int(fx_id) // 128, channel])

    def send_query_patch_name(self):
        self._send_sysex([FM3_MODEL_ID, QUERY_PATCH_NAME, 0x7F, 0x7F])

    def send_query_scene_name(self):
        self._send_sysex([FM3_MODEL_ID, QUERY_SCENE_NAME, 0x7F])

    def send_get_tempo(self):
        self._send_sysex([FM3_MODEL_ID, SET_GET_TEMPO, 0x7F, 0x7F])

    def send_looper_button(self, btn):
        self._send_sysex([FM3_MODEL_ID, LOOPER_FUNC, btn])

    def send_get_looper(self):
        self._send_sysex([FM3_MODEL_ID, LOOPER_FUNC, 0x7F])

    def send_patch_inc(self):
        self.midi.send(ControlChange(41, 127))

    def send_patch_dec(self):
        self.midi.send(ControlChange(42, 127))

    # --------------------------------------------------------
    # MIDI receive — 자체 SysEx 프레임 파서
    # --------------------------------------------------------
    def process_midi_in(self):
        chunk = self.uart.read(256)
        if chunk:
            self.rx_buf += chunk
        buf = self.rx_buf
        if not buf:
            return
        while True:
            start = buf.find(b'\xf0')
            if start < 0:
                buf = b''
                break
            end = buf.find(b'\xf7', start + 1)
            if end < 0:
                if start > 0:
                    buf = buf[start:]
                if len(buf) > 1024:
                    buf = b''
                break
            frame = buf[start + 1:end]
            buf = buf[end + 1:]
            self._handle_frame(frame)
        self.rx_buf = buf

    def _handle_frame(self, frame):
        if len(frame) < 5:
            return
        if frame[0] != 0x00 or frame[1] != 0x01 or frame[2] != 0x74:
            return
        self._handle_sysex(frame[3:])

    def _handle_sysex(self, data):
        if len(data) < 2 or data[0] != FM3_MODEL_ID:
            return
        func = data[1]

        if func == TAP_TEMPO_FUNC:
            self.tap.on_tap()
            return

        if func == STATUS_DUMP:
            self._parse_status_dump(data)
            return

        if func == SET_SCENE:
            if len(data) >= 3:
                new_scene = data[2]
                if new_scene != self.current_scene:
                    self.current_scene = new_scene
                    self._update_scene_leds()
                    self.send_query_scene_name()
                    self.display_dirty = True
            return

        if func == QUERY_PATCH_NAME:
            if len(data) >= 36:
                num = data[2] + data[3] * 128
                name = "".join(chr(b) for b in data[4:36] if 32 <= b < 127).strip()
                if name != self.patch_name:
                    self.patch_name = name
                    self.display_dirty = True
                if num != self.patch_number:
                    self.patch_number = num
                    self.send_get_scene()
                    self.send_query_scene_name()
            return

        if func == QUERY_SCENE_NAME:
            if len(data) >= 35:
                name = "".join(chr(b) for b in data[3:35] if 32 <= b < 127).strip()
                if name != self.scene_name:
                    self.scene_name = name
                    self.display_dirty = True
            return

        if func == TUNER_FUNC:
            payload_len = len(data) - 2
            if payload_len >= 3:
                if time.monotonic() < self.tuner_ignore_until:
                    return  # 사용자가 방금 off한 직후의 잔여 push 무시
                self.tuner_note = data[2]
                self.tuner_string = data[3]
                self.tuner_cents = data[4]
                self.tuner_active = True
                self.tuner_last_data = time.monotonic()
                self.display_dirty = True
            elif payload_len >= 1 and data[2] == 0:
                self.tuner_active = False
                self.display_dirty = True
            return

        if func == SET_GET_TEMPO:
            if len(data) >= 4:
                new_bpm = data[2] + data[3] * 128
                if new_bpm != self.tempo_bpm:
                    self.tempo_bpm = new_bpm
                    self.tap.interval = 60.0 / new_bpm if new_bpm > 0 else 0.5
                    self.display_dirty = True
            return

        if func == LOOPER_FUNC:
            if len(data) >= 3:
                new_state = data[2]
                if new_state != self.looper_state:
                    self.looper_state = new_state
                    for i in self.looper_buttons:
                        self._update_button_leds(i)
            return

        if func == SET_CHANNEL:
            if len(data) >= 5:
                fx_id = data[2] + 128 * data[3]
                self.fx_channels[fx_id] = data[4]
                self._update_fx_leds(fx_id)
            return

    def _parse_status_dump(self, data):
        if len(data) < 4 or data[1] != STATUS_DUMP:
            return
        self.fx_states.clear()
        self.fx_channels.clear()
        self.fx_num_channels.clear()
        packets = data[2:-1]
        for i in range(0, len(packets) - 2, 3):
            fx_id = int(packets[i]) + 128 * int(packets[i + 1])
            dd = packets[i + 2]
            self.fx_states[fx_id] = bool(dd & 0x01)
            self.fx_channels[fx_id] = (dd >> 1) & 0x07
            self.fx_num_channels[fx_id] = (dd >> 4) & 0x07
        self._update_all_button_leds()
        sig = len(self.fx_states) * 100000 + sum(self.fx_states)
        if sig != self._dump_sig:
            if self._dump_sig is not None and not self.name_query_at:
                self.name_query_at = time.monotonic() + 0.3
            self._dump_sig = sig

    # --------------------------------------------------------
    # Button handling
    # --------------------------------------------------------
    def process_buttons(self):
        now = time.monotonic()
        if self.edit_mode:
            self._process_buttons_edit(now)
            return
        while event := self.keys.events.get():
            key = event.key_number
            if event.pressed:
                self.press_times[key] = now
                self.hold_fired.discard(key)
                # tap tempo는 press 시점 즉시 발화 (release 대기 없음)
                if self.config[key].get("press", {}).get("type") == "tap_tempo":
                    self.send_tap_tempo()
                    self._show_temp("BPM", "%d" % self.tempo_bpm)
            elif event.released:
                press_time = self.press_times.pop(key, None)
                if press_time is None:
                    # press 기록이 없는 release (Edit Mode 진입/종료 중 눌려 있던 키 등) → 무시
                    self.hold_fired.discard(key)
                    continue
                if key in self.hold_fired:
                    # timeout 모드에서 이미 hold 발동됨 → release는 무시
                    self.hold_fired.discard(key)
                    continue
                duration = now - press_time
                if duration >= self.hold_time:
                    self._handle_action(key, "hold")
                else:
                    self._handle_action(key, "press")

        # timeout 모드: 누르고 있는 동안 hold_time 도달 시 즉시 발동
        if self.hold_mode == "timeout" and self.press_times:
            for key, t0 in list(self.press_times.items()):
                if key not in self.hold_fired and now - t0 >= self.hold_time:
                    self.hold_fired.add(key)
                    self._handle_action(key, "hold")

    EDIT_EXIT_HOLD = 1.0  # Edit Mode에서 DN 스위치를 이 시간 이상 누르면 저장 후 종료 (issue #4)

    def _process_buttons_edit(self, now):
        """Edit Mode 중 풋스위치: 실제 동작 대신 편집 단축키.
        - Press  → 현재 페이지 해당 스위치의 Press 액션 편집 화면으로
        - Hold   → 해당 스위치의 Hold 액션 편집 화면으로
        - Up+Dn 동시에 1초 이상 → Edit Mode 저장 후 종료 (issue #4)"""
        # 종료 콤보: Up(4)+Dn(9)이 모두 눌린 채 둘 다 EDIT_EXIT_HOLD 경과
        if 4 in self.press_times and 9 in self.press_times:
            if (now - self.press_times[4] >= self.EDIT_EXIT_HOLD
                    and now - self.press_times[9] >= self.EDIT_EXIT_HOLD):
                self._exit_edit_mode()   # press_times/hold_fired 비워짐 → 이후 release 무시
                self.keys.events.clear()
                return
        while event := self.keys.events.get():
            key = event.key_number
            if event.pressed:
                self.press_times[key] = now
            elif event.released:
                t0 = self.press_times.pop(key, None)
                if t0 is None:
                    continue
                # 콤보 상대 키가 아직 눌려 있으면 (콤보 시도 중 한 발 먼저 뗌) 단축키로 처리하지 않음
                if (key == 4 and 9 in self.press_times) or (key == 9 and 4 in self.press_times):
                    continue
                duration = now - t0
                self._edit_jump_to_switch(key, "hold" if duration >= self.hold_time else "press")

    def _edit_jump_to_switch(self, idx, which):
        """현재 페이지의 스위치 idx / press|hold 파라미터 화면으로 바로 이동"""
        self.edit_page = self.page_idx
        self.edit_btn_idx = idx
        self.edit_press_idx = 0 if which == "press" else 1
        self.edit_screen = self.SCR_PARAM
        self.edit_cursor = 0
        self.edit_editing_value = False
        self.edit_led_idx = 3
        self._refresh_edit_preview()
        self.display_dirty = True

    def _handle_action(self, idx, key):
        cfg = self.config[idx]
        action = cfg.get(key, {})
        atype = action.get("type", "none")

        # Hold가 비어 있고 Press가 rotation sub-function을 가진 effect면 채널 순환
        if key == "hold" and atype == "none":
            pa = cfg.get("press", {})
            if pa.get("type") == "effect" and pa.get("rotation"):
                self._do_channel_rotation(idx, pa)
            return

        if atype in ("none", "tap_tempo"):
            return

        if atype == "scene":
            scene_num = action.get("number", 1) - 1
            self.send_set_scene(scene_num)
            self.current_scene = scene_num
            self._update_scene_leds()
            self._show_temp("SCENE", "%d" % (scene_num + 1))

        elif atype == "effect":
            fx_id = EFFECT_IDS.get(action.get("effect", ""))
            if fx_id is None:
                return
            was_bypassed = self.fx_states.get(fx_id, True)
            self.send_set_fx_status(fx_id, 0 if was_bypassed else 1)
            self.fx_states[fx_id] = not was_bypassed
            self._update_fx_leds(fx_id)
            self._show_temp(action.get("effect", ""), "ON" if was_bypassed else "OFF")

        elif atype == "channel_select":
            fx_id = EFFECT_IDS.get(action.get("effect", ""))
            if fx_id is None:
                return
            ch = action.get("channel", 0)
            self.send_set_channel(fx_id, ch)
            self.fx_channels[fx_id] = ch
            self._update_fx_leds(fx_id)
            self._show_temp(action.get("effect", ""), "CH.%s" % chr(65 + ch))

        elif atype == "looper":
            btn = action.get("button", 0)
            self.send_looper_button(btn)
            self.send_get_looper()
            if btn == 2:  # UNDO — tap 타입 순간 점등
                self._flash_tap(idx)
            name = LOOPER_BTN_NAMES[btn] if btn < len(LOOPER_BTN_NAMES) else "?"
            self._show_temp("LOOPER", name)

        elif atype == "tuner":
            self.tuner_active = not self.tuner_active
            self.send_tuner(self.tuner_active)
            if not self.tuner_active:
                # off 직후 버퍼에 남은 tuner push가 다시 켜는 것을 방지
                self.tuner_ignore_until = time.monotonic() + 0.5
                self.tuner_last_data = 0
            self._show_temp("TUNER", "ON" if self.tuner_active else "OFF")
            self._update_button_leds(idx)

        elif atype == "preset_inc":
            self.send_patch_inc()
            self._show_temp("PRESET", "+")
            self._flash_tap(idx)
            self.name_query_at = time.monotonic() + 0.4

        elif atype == "preset_dec":
            self.send_patch_dec()
            self._show_temp("PRESET", "-")
            self._flash_tap(idx)
            self.name_query_at = time.monotonic() + 0.4

        elif atype == "page_inc":
            self._flash_tap(idx)
            self._change_page(1)

        elif atype == "page_dec":
            self._flash_tap(idx)
            self._change_page(-1)

    def _do_channel_rotation(self, idx, pa):
        fx_id = EFFECT_IDS.get(pa.get("effect", ""))
        if fx_id is None:
            return
        cur = self.fx_channels.get(fx_id, 0)
        avail = sorted(pa.get("channels", [0, 1, 2, 3])) or [0, 1, 2, 3]
        try:
            i = avail.index(cur)
        except ValueError:
            i = -1
        new_ch = avail[(i + 1) % len(avail)]
        self.send_set_channel(fx_id, new_ch)
        self.fx_channels[fx_id] = new_ch
        self._update_fx_leds(fx_id)
        self._show_temp(pa.get("effect", ""), "CH.%s" % chr(65 + new_ch))

    def _flash_tap(self, idx):
        """tap 타입: press 순간 activation 색으로 잠깐 점등"""
        self.tap_flash_until[idx] = time.monotonic() + TAP_FLASH_SEC
        self._update_button_leds(idx)

    # --------------------------------------------------------
    # LED engine
    # --------------------------------------------------------
    def _update_all_button_leds(self):
        for i in range(NUM_BUTTONS):
            self._update_button_leds(i)

    def _update_scene_leds(self):
        for i in self.scene_buttons:
            self._update_button_leds(i)

    def _update_fx_leds(self, fx_id):
        for i in self.fx_to_btn.get(fx_id, ()):
            self._update_button_leds(i)

    def _update_button_leds(self, idx):
        cfg = self.config[idx]
        pa = cfg.get("press", {})
        ha = cfg.get("hold", {})
        pt = pa.get("type", "none")
        ht = ha.get("type", "none")

        # Edit Mode에서 색상 편집 중인 버튼: FM3 상태와 무관하게 편집 중인 색을 강제 점등
        if self.edit_mode and idx == self.edit_btn_idx and self.edit_page == self.page_idx:
            preview = self._edit_preview_colors()
            if preview is not None:
                self.leds.set_button_leds(idx, *preview)
                return

        # tap tempo: 메인 루프에서 박자 점멸 처리
        if pt == "tap_tempo":
            return

        # tap 타입 순간 점등 중이면 activation 색
        if time.monotonic() < self.tap_flash_until[idx]:
            src = pa if pt in self.TAP_TYPES or (pt == "looper" and pa.get("button") == 2) else ha
            leds = action_colors(src)
            self.leds.set_button_leds(idx, *leds)
            return

        # Press가 상태형이면 Press 색상이 LED 주도.
        # 예외(issue #2): Hold도 상태형이고 Press는 비활성·Hold는 활성이면 Hold 색을 켠다
        # (예: Press=scene1 Red, Hold=scene2 Green → scene2 활성 시 Green)
        if pt in self.STATE_TYPES:
            if ht in self.STATE_TYPES and not self._state_active(pa) and self._state_active(ha):
                leds = self._state_leds(ha)
            else:
                leds = self._state_leds(pa)
            self.leds.set_button_leds(idx, *leds)
            return

        # Press가 tap 타입: 평소 deactivation 색
        if pt in self.TAP_TYPES:
            leds = [color_off(c) for c in action_colors(pa)]
            self.leds.set_button_leds(idx, *leds)
            return

        # Press가 tuner: 상태 표시
        if pt == "tuner":
            base = action_colors(pa)
            leds = base if self.tuner_active else [color_off(c) for c in base]
            self.leds.set_button_leds(idx, *leds)
            return

        # Press가 none → Hold 액션이 상태형이면 그것을 표시 (예외 허용)
        if pt == "none" and ht in self.STATE_TYPES:
            leds = self._state_leds(ha)
            self.leds.set_button_leds(idx, *leds)
            return
        if pt == "none" and ht in self.TAP_TYPES:
            leds = [color_off(c) for c in action_colors(ha)]
            self.leds.set_button_leds(idx, *leds)
            return

        self.leds.set_button_color(idx, (0, 0, 0))

    def _state_active(self, a):
        """상태형 액션이 현재 '활성'인지 (LED 밝게 켤 조건). 프리셋에 없는 블록은 비활성."""
        t = a.get("type", "none")
        if t == "effect":
            fx_id = EFFECT_IDS.get(a.get("effect", ""))
            return fx_id is not None and fx_id in self.fx_states and not self.fx_states[fx_id]
        if t == "scene":
            return self.current_scene == a.get("number", 1) - 1
        if t == "channel_select":
            fx_id = EFFECT_IDS.get(a.get("effect", ""))
            return (fx_id is not None and fx_id in self.fx_states
                    and self.fx_channels.get(fx_id, 0) == a.get("channel", 0))
        if t == "looper":
            btn = a.get("button", 0)
            if btn == 2:
                return False
            mask = LOOPER_LED_MASKS[btn] if btn < len(LOOPER_LED_MASKS) else 0
            return bool(self.looper_state & mask)
        return False

    def _state_leds(self, a):
        """상태형 액션의 현재 LED 3색"""
        t = a.get("type", "none")
        base = action_colors(a)

        if t == "effect":
            fx_id = EFFECT_IDS.get(a.get("effect", ""))
            if fx_id is None or fx_id not in self.fx_states:
                return [(0, 0, 0)] * 3  # 프리셋에 없는 블록 → 완전 소등
            bypassed = self.fx_states[fx_id]
            if a.get("rotation"):
                # sub-function: 현재 채널 색이 activation 색
                ch = self.fx_channels.get(fx_id, 0)
                ch_colors = a.get("ch_colors", ["GREEN", "YELLOW", "ORANGE", "RED"])
                base = color_leds(ch_colors[ch]) if ch < len(ch_colors) else [(0, 0, 0)] * 3
            return base if not bypassed else [color_off(c) for c in base]

        if t == "scene":
            on = (self.current_scene == a.get("number", 1) - 1)
            return base if on else [color_off(c) for c in base]

        if t == "channel_select":
            fx_id = EFFECT_IDS.get(a.get("effect", ""))
            if fx_id is None or fx_id not in self.fx_states:
                return [(0, 0, 0)] * 3
            on = (self.fx_channels.get(fx_id, 0) == a.get("channel", 0))
            return base if on else [color_off(c) for c in base]

        if t == "looper":
            btn = a.get("button", 0)
            if btn == 0:
                # REC: recording=color, overdubbing=color2, else dim(color)
                if self.looper_state & LOOPER_BIT_OVERDUB:
                    c = pal(a.get("color2", "ORANGE"))
                    return [c, c, c]
                if self.looper_state & LOOPER_BIT_REC:
                    return base
                return [color_off(c) for c in base]
            if btn == 2:  # UNDO: tap 타입 — 평소 dim
                return [color_off(c) for c in base]
            mask = LOOPER_LED_MASKS[btn] if btn < len(LOOPER_LED_MASKS) else 0
            return base if (self.looper_state & mask) else [color_off(c) for c in base]

        return [color_off(c) for c in base]

    def _update_tap_tempo_led(self, idx):
        pa = self.config[idx].get("press", {})
        c = action_colors(pa)
        if self.tap.is_flashing():
            self.leds.set_button_leds(idx, *c)
        else:
            self.leds.set_button_leds(idx, *[color_off(x) for x in c])

    # --------------------------------------------------------
    # Display
    # --------------------------------------------------------
    def _show_temp(self, name, state="", duration=1.5):
        self.display_temp_name = name
        self.display_temp_state = state
        self.display_temp_until = time.monotonic() + duration
        self.display_dirty = True

    def _set_label(self, lbl, text):
        if lbl.text != text:
            lbl.text = text

    # 이름 라벨 자동 축소: 13자 이하 scale 3 / 초과 scale 2 (최대 20자)
    NAME_MAX_S3 = 13   # 240px / (6px*3)
    NAME_MAX_S2 = 20   # 240px / (6px*2)

    def _set_name_label(self, lbl, text):
        if len(text) <= self.NAME_MAX_S3:
            scale = 3
        else:
            scale = 2
            text = text[:self.NAME_MAX_S2]
        if lbl.scale != scale:
            lbl.scale = scale
            # scale 변경 시 anchor 재적용 (displayio가 위치 재계산하도록)
            lbl.anchored_position = lbl.anchored_position
        if lbl.text != text:
            lbl.text = text

    def _update_display(self):
        now = time.monotonic()

        if self.tuner_active:
            if self.display_dirty:
                self.display_dirty = False
                if self.tuner_last_data and now - self.tuner_last_data < 3.0:
                    note = NOTE_NAMES[self.tuner_note % 12]
                    self._set_label(self.lbl_tuner_info,
                                    "Note: %s  Str: %d" % (note, self.tuner_string + 1))
                    cents = self.tuner_cents - 63
                    if cents == 0:
                        self._set_label(self.lbl_tuner_cents, "IN TUNE")
                        self.lbl_tuner_cents.color = 0x00FF00
                    else:
                        self._set_label(self.lbl_tuner_cents, "%+d cents" % cents)
                        self.lbl_tuner_cents.color = 0xFFFF00
                else:
                    # push 데이터 없음 (FM3 Send Realtime SysEx off) — 화면은 FM3 본체 참조
                    self._set_label(self.lbl_tuner_info, "(see FM3 screen)")
                    self._set_label(self.lbl_tuner_cents, "Hold to exit")
                    self.lbl_tuner_cents.color = 0x888888
                self._show_group(self.grp_tuner)
                self.display.refresh()
            return

        if self.display_temp_name and now >= self.display_temp_until:
            self.display_temp_name = None
            self.display_temp_state = ""
            self.display_dirty = True

        if self.display_dirty:
            self.display_dirty = False
            page_name = self.pages[self.page_idx].get("name", "")[:6]
            self._set_label(self.lbl_page, "P%d %s" % (self.page_idx + 1, page_name))
            self._set_label(self.lbl_bpm, "BPM:%d" % self.tempo_bpm)
            # scale 3 → 최대 13자
            self._set_name_label(self.lbl_patch, self.patch_name or "---")
            self._set_name_label(self.lbl_scene, self.scene_name or "---")
            if self.display_temp_name:
                self._set_label(self.lbl_action_name, self.display_temp_name)
                self._set_label(self.lbl_action_state, self.display_temp_state)
            else:
                self._set_label(self.lbl_action_name, "")
                self._set_label(self.lbl_action_state, "")
            self._show_group(self.grp_normal)
            self.display.refresh()
        else:
            self._show_group(self.grp_normal)

    # --------------------------------------------------------
    # Edit mode (Rotary Encoder) — 화면 기반 네비게이션
    #   MAIN   : Switch Setup(페이지 선택) / Global Settings / Exit
    #   SWITCH : 10개 스위치 테이블 (SW | Press | Hold) + Back
    #   ACTION : 선택 스위치의 Press / Hold / Back
    #   PARAM  : 액션 파라미터 (이름 : 값) 테이블
    #   GLOBAL : HoldTime / HoldAt / StartPg / Back
    # --------------------------------------------------------
    EDIT_TYPES = ["effect", "channel_select", "scene", "looper", "tap_tempo", "tuner",
                  "preset_inc", "preset_dec", "page_inc", "page_dec", "none"]
    EFFECT_LIST = sorted(EFFECT_IDS.keys())
    BTN_ABBREV = ("1", "2", "3", "4", "Up", "A", "B", "C", "D", "Dn")
    LED_LABELS = ("L1", "L2", "L3", "ALL")
    SCR_MAIN, SCR_SWITCH, SCR_ACTION, SCR_PARAM, SCR_GLOBAL = 0, 1, 2, 3, 4
    EDIT_ROWS = 13  # 화면 행 수 (scale 2, 행간 17px)
    # 페이지 이름 편집: 회전 = 문자 순환, 클릭 = 다음 칸, 마지막 칸 클릭 = 확정
    PAGE_NAME_LEN = 6
    NAME_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_+./"

    def _commit_page_name(self):
        name = "".join(self.edit_name).strip()
        if not name:
            name = "P%d" % (self.edit_page + 1)
        self.pages[self.edit_page]["name"] = name
        if self.edit_page == self.page_idx:
            self.display_dirty = True  # 좌상단 페이지 표시 갱신용 (edit 종료 후 반영)

    def process_encoder(self):
        sw_state = not self.encoder_sw.value
        now = time.monotonic()
        if sw_state and not self.encoder_sw_pressed:
            self.encoder_sw_pressed = True
            self.encoder_sw_press_time = now
        elif not sw_state and self.encoder_sw_pressed:
            self.encoder_sw_pressed = False
            duration = now - self.encoder_sw_press_time
            if duration >= 1.0:
                if self.edit_mode:
                    self._edit_back()   # 한 단계 위로, MAIN에서는 종료(저장)
                else:
                    self._enter_edit_mode()
            elif self.edit_mode:
                self._edit_click()

        pos = self.encoder.position
        delta = pos - self.encoder_last_pos
        if delta != 0:
            self.encoder_last_pos = pos
            if self.edit_mode:
                self._edit_rotate(delta)
                self.edit_redraw_at = now + self.EDIT_REDRAW_DEBOUNCE
            else:
                self._change_page(delta)

    def _enter_edit_mode(self):
        self.edit_mode = True
        self.press_times.clear(); self.hold_fired.clear()  # 진입 중 눌린 스위치 무시
        self.edit_screen = self.SCR_MAIN
        self.edit_cursor = 0
        self.edit_editing_value = False
        self.edit_page = self.page_idx       # Switch Setup 대상 페이지 (기본 현재 페이지)
        self.edit_btn_idx = 0
        self.edit_press_idx = 0
        self.edit_led_idx = 3
        self.display_dirty = True

    def _exit_edit_mode(self):
        self.edit_mode = False
        self.press_times.clear(); self.hold_fired.clear()
        self.full_config["hold_time"] = self.hold_time
        self.full_config["hold_mode"] = self.hold_mode
        self.full_config["start_page"] = self.start_page
        save_config(self.full_config)
        # 편집 대상 페이지가 현재 페이지와 다르면 lookup은 현재 페이지 기준으로 재구성
        self.config = self.pages[self.page_idx]["buttons"]
        self._build_lookups()
        self._update_all_button_leds()
        self.display_dirty = True

    def _copy_page(self, src, dst):
        """페이지 src의 버튼/이름을 dst로 깊은 복사 (issue #3). src==dst면 무시."""
        if src == dst:
            return
        self.pages[dst]["buttons"] = _copy_json(self.pages[src]["buttons"])
        self.pages[dst]["name"] = self.pages[src].get("name", "")
        if dst == self.page_idx:
            self.config = self.pages[self.page_idx]["buttons"]
            self._build_lookups()
            self._update_all_button_leds()

    # ---- 편집 대상 접근 ----
    def _edit_buttons(self):
        return self.pages[self.edit_page]["buttons"]

    def _cur_action(self):
        key = "press" if self.edit_press_idx == 0 else "hold"
        return self._edit_buttons()[self.edit_btn_idx].setdefault(key, _none())

    # ---- 화면별 행 목록 (Back 행 없음 — 로터리 길게 누름 = Back) ----
    def _screen_items(self):
        s = self.edit_screen
        if s == self.SCR_MAIN:
            return ["Switch Setup", "Copy Page", "Global Settings", "Exit"]
        if s == self.SCR_SWITCH:
            # 0 = 헤더(페이지 이름 편집), 1..10 = 스위치
            return ["__hdr__"] + list(self.BTN_ABBREV)
        if s == self.SCR_ACTION:
            return ["Press", "Hold"]
        if s == self.SCR_GLOBAL:
            return ["HoldTime", "HoldAt", "StartPg"]
        return self._get_edit_params()

    def _edit_back(self):
        """로터리 길게 누름: 편집 중이면 편집 취소(값은 유지), 아니면 한 단계 위로.
        MAIN에서는 저장 후 종료."""
        s = self.edit_screen
        if self.edit_editing_value:
            # 값 편집 모드 해제만 (페이지 이름 편집 중이면 현재까지 입력 확정)
            if s == self.SCR_SWITCH and self.edit_cursor == 0:
                self._commit_page_name()
            self.edit_editing_value = False
        elif s == self.SCR_MAIN:
            self._exit_edit_mode()
            return
        elif s == self.SCR_SWITCH:
            self.edit_screen = self.SCR_MAIN
            self.edit_cursor = 0
        elif s == self.SCR_ACTION:
            self.edit_screen = self.SCR_SWITCH
            self.edit_cursor = self.edit_btn_idx + 1
        elif s == self.SCR_PARAM:
            self.edit_screen = self.SCR_ACTION
            self.edit_cursor = self.edit_press_idx
        elif s == self.SCR_GLOBAL:
            self.edit_screen = self.SCR_MAIN
            self.edit_cursor = 2
        self._refresh_edit_preview()
        self.display_dirty = True

    def _get_edit_params(self):
        a = self._cur_action()
        t = a.get("type", "none")
        if t == "effect":
            p = ["Type", "Target"]
            if self.edit_press_idx == 0:
                if a.get("rotation"):
                    # Rotate ON: 색은 채널별(Col.A~D)로 결정 → Color 행 숨김
                    p += ["Rotate", "Chans", "Col.A", "Col.B", "Col.C", "Col.D"]
                else:
                    p += ["Color", "Rotate"]
            else:
                p.append("Color")
            return p
        if t == "channel_select":
            return ["Type", "Target", "Chan", "Color"]
        if t == "scene":
            return ["Type", "Target", "Color"]
        if t == "looper":
            p = ["Type", "Target", "Color"]
            if a.get("button", 0) == 0:
                p.append("Col.OD")
            return p
        if t == "none":
            return ["Type"]
        return ["Type", "Color"]

    # ---- 입력 처리 ----
    def _edit_click(self):
        s = self.edit_screen
        items = self._screen_items()
        item = items[self.edit_cursor]

        if s == self.SCR_MAIN:
            if item == "Switch Setup":
                if not self.edit_editing_value:
                    self.edit_editing_value = True   # 회전 = 페이지 선택
                else:
                    self.edit_editing_value = False
                    self.edit_screen = self.SCR_SWITCH
                    self.edit_cursor = 1   # 첫 스위치 (0 = 페이지 이름 헤더)
            elif item == "Copy Page":
                # issue #3: 소스 = Switch 행에서 고른 페이지(edit_page), 회전으로 대상 선택, 클릭 = 복사
                if not self.edit_editing_value:
                    self.edit_editing_value = True
                    # 기본 대상: 소스 다음 페이지
                    self.copy_dst = (self.edit_page + 1) % len(self.pages)
                else:
                    self._copy_page(self.edit_page, self.copy_dst)
                    self.edit_editing_value = False
            elif item == "Global Settings":
                self.edit_screen = self.SCR_GLOBAL
                self.edit_cursor = 0
            else:
                self._exit_edit_mode()
                return

        elif s == self.SCR_SWITCH:
            if self.edit_cursor == 0:
                # 헤더(페이지 이름) 클릭: 편집 시작 / 편집 중이면 다음 칸
                if not self.edit_editing_value:
                    name = self.pages[self.edit_page].get("name", "")
                    self.edit_name = list((name + " " * self.PAGE_NAME_LEN)[:self.PAGE_NAME_LEN])
                    self.edit_name_pos = 0
                    self.edit_editing_value = True
                else:
                    # 클릭 = 다음 칸. 마지막 칸에서 클릭 = 확정 (Color 편집과 동일한 흐름)
                    self.edit_name_pos += 1
                    if self.edit_name_pos >= self.PAGE_NAME_LEN:
                        self._commit_page_name()
                        self.edit_editing_value = False
            else:
                self.edit_btn_idx = self.edit_cursor - 1
                self.edit_screen = self.SCR_ACTION
                self.edit_cursor = 0

        elif s == self.SCR_ACTION:
            self.edit_press_idx = self.edit_cursor
            self.edit_screen = self.SCR_PARAM
            self.edit_cursor = 0
            self.edit_editing_value = False

        elif s == self.SCR_GLOBAL:
            self.edit_editing_value = not self.edit_editing_value

        elif s == self.SCR_PARAM:
            if item == "Color" or item.startswith("Col.") and item != "Col.OD":
                # 클릭: 편집 진입(대상 ALL) → 클릭마다 대상 순환 ALL→L1→L2→L3 → 다시 클릭 = 종료
                # 회전 = 색 순환. 길게 누름 = 즉시 종료. (Color 및 Col.A~D 공통)
                if not self.edit_editing_value:
                    self.edit_editing_value = True
                    self.edit_led_idx = 3
                elif self.edit_led_idx == 2:      # L3에서 클릭 → 편집 종료
                    self.edit_editing_value = False
                else:
                    self.edit_led_idx = (self.edit_led_idx + 1) % 4  # 3(ALL)→0→1→2
            elif item == "Chans":
                # 편집 모드: 회전 = 서브커서 A→B→C→D→[Done] 이동, 클릭 = 토글 (Done에서 클릭 = 종료)
                if not self.edit_editing_value:
                    self.edit_editing_value = True
                    self.edit_ch_idx = 0
                elif self.edit_ch_idx == 4:       # Done
                    self.edit_editing_value = False
                else:
                    a = self._cur_action()
                    ch = self.edit_ch_idx
                    channels = list(a.get("channels", [0, 1, 2, 3]))
                    if ch in channels:
                        if len(channels) > 1:   # 최소 1채널 유지
                            channels.remove(ch)
                    else:
                        channels.append(ch)
                    a["channels"] = sorted(channels)
            else:
                self.edit_editing_value = not self.edit_editing_value

        self._refresh_edit_preview()
        self.display_dirty = True

    def _edit_rotate(self, delta):
        s = self.edit_screen
        if self.edit_editing_value:
            if s == self.SCR_MAIN:
                item = self._screen_items()[self.edit_cursor]
                if item == "Copy Page":
                    self.copy_dst = (self.copy_dst + delta) % len(self.pages)
                else:
                    self.edit_page = (self.edit_page + delta) % len(self.pages)
            elif s == self.SCR_SWITCH:
                # 헤더(페이지 이름) 편집: 커서 위치 문자 순환
                ch = self.edit_name[self.edit_name_pos]
                i = self.NAME_CHARS.find(ch)
                if i < 0:
                    i = 0
                self.edit_name[self.edit_name_pos] = self.NAME_CHARS[(i + delta) % len(self.NAME_CHARS)]
            elif s == self.SCR_GLOBAL:
                self._edit_change_global(delta)
            elif s == self.SCR_PARAM:
                self._edit_change_value(delta)
        else:
            items = self._screen_items()
            prev = self.edit_cursor
            self.edit_cursor = (self.edit_cursor + delta) % len(items)
            if s == self.SCR_SWITCH:
                # 테이블에서 커서 이동 시 해당 스위치 LED 미리보기 대상 갱신 (0=헤더)
                if 1 <= prev <= NUM_BUTTONS:
                    self.edit_btn_idx = prev - 1
                    self._refresh_edit_preview()
                if 1 <= self.edit_cursor <= NUM_BUTTONS:
                    self.edit_btn_idx = self.edit_cursor - 1
        self._refresh_edit_preview()
        self.display_dirty = True

    def _refresh_edit_preview(self):
        """편집 대상 페이지가 현재 페이지일 때만 발밑 LED 미리보기"""
        if self.edit_page == self.page_idx and self.edit_btn_idx < NUM_BUTTONS:
            self._update_button_leds(self.edit_btn_idx)

    def _edit_change_global(self, delta):
        item = self._screen_items()[self.edit_cursor]
        if item == "HoldTime":
            v = round(self.hold_time + delta * 0.1, 1)
            self.hold_time = max(HOLD_TIME_MIN, min(HOLD_TIME_MAX, v))
        elif item == "HoldAt":
            i = HOLD_MODES.index(self.hold_mode)
            self.hold_mode = HOLD_MODES[(i + delta) % len(HOLD_MODES)]
        elif item == "StartPg":
            n = len(self.pages)
            self.start_page = ((self.start_page + 1 + delta) % (n + 1)) - 1

    def _edit_change_value(self, delta):
        a = self._cur_action()
        params = self._get_edit_params()
        item = params[self.edit_cursor]

        if item == "Type":
            cur = a.get("type", "none")
            i = self.EDIT_TYPES.index(cur) if cur in self.EDIT_TYPES else 0
            new_t = self.EDIT_TYPES[(i + delta) % len(self.EDIT_TYPES)]
            color = a.get("color", "OFF")
            a.clear()
            a["type"] = new_t
            a["color"] = color if new_t != "none" else "OFF"
            if new_t in ("effect", "channel_select"):
                a["effect"] = self.EFFECT_LIST[0]
                if new_t == "channel_select":
                    a["channel"] = 0
            elif new_t == "scene":
                a["number"] = 1
            elif new_t == "looper":
                a["button"] = 0
            self.edit_cursor = 0

        elif item == "Target":
            self._edit_target(a, delta)

        elif item == "Chan":
            a["channel"] = (a.get("channel", 0) + delta) % 4

        elif item == "Rotate":
            if not a.get("rotation", False):
                a["rotation"] = True
                a.setdefault("channels", [0, 1, 2, 3])
                a.setdefault("ch_colors", ["GREEN", "YELLOW", "ORANGE", "RED"])
            else:
                a.pop("rotation", None)

        elif item == "Color":
            self._edit_color(a, delta)

        elif item == "Col.OD":
            a["color2"] = self._next_pal(a.get("color2", "ORANGE"), delta)

        elif item == "Chans":
            # 회전 = A~D 서브커서 이동 (토글은 클릭)
            self.edit_ch_idx = (self.edit_ch_idx + delta) % 5   # 0-3 = A-D, 4 = Done

        elif item.startswith("Col."):
            ch = "ABCD".index(item[-1])
            cc = list(a.get("ch_colors", ["GREEN", "YELLOW", "ORANGE", "RED"]))
            while len(cc) < 4:
                cc.append("OFF")
            cc[ch] = self._cycle_color_spec(cc[ch], delta)   # ALL 또는 L1~L3 대상
            a["ch_colors"] = cc

    def _next_pal(self, name, delta):
        i = PALETTE_NAMES.index(name) if name in PALETTE_NAMES else 0
        return PALETTE_NAMES[(i + delta) % len(PALETTE_NAMES)]

    def _edit_preview_colors(self):
        """PARAM 화면에서 커서가 색상 항목이면 그 색(3 LED), 아니면 None."""
        if self.edit_screen != self.SCR_PARAM:
            return None
        params = self._get_edit_params()
        if self.edit_cursor >= len(params):
            return None
        item = params[self.edit_cursor]
        a = self._cur_action()
        if item == "Color":
            return action_colors(a)
        if item == "Col.OD":
            c = pal(a.get("color2", "ORANGE"))
            return [c, c, c]
        if item.startswith("Col."):
            ch = "ABCD".index(item[-1])
            cc = a.get("ch_colors", ["GREEN", "YELLOW", "ORANGE", "RED"])
            return color_leds(cc[ch]) if ch < len(cc) else [(0, 0, 0)] * 3
        return None

    def _cycle_color_spec(self, spec, delta):
        """색 지정값(문자열 or 3-리스트)을 현재 편집 대상(edit_led_idx: 3=ALL, 0~2=L1~L3)에 따라 순환"""
        if self.edit_led_idx == 3:
            cur = spec if isinstance(spec, str) else (spec[0] if spec else "OFF")
            return self._next_pal(cur, delta)          # ALL → 문자열로 통일
        lst = list(spec) if isinstance(spec, list) else [spec, spec, spec]
        while len(lst) < 3:
            lst.append(lst[-1] if lst else "OFF")
        lst[self.edit_led_idx] = self._next_pal(lst[self.edit_led_idx], delta)
        return lst

    def _edit_color(self, a, delta):
        a["color"] = self._cycle_color_spec(a.get("color", "OFF"), delta)

    def _edit_target(self, a, delta):
        t = a.get("type", "none")
        if t in ("effect", "channel_select"):
            cur = a.get("effect", "")
            i = self.EFFECT_LIST.index(cur) if cur in self.EFFECT_LIST else 0
            a["effect"] = self.EFFECT_LIST[(i + delta) % len(self.EFFECT_LIST)]
        elif t == "scene":
            a["number"] = max(1, min(8, a.get("number", 1) + delta))
        elif t == "looper":
            a["button"] = (a.get("button", 0) + delta) % len(LOOPER_BTN_NAMES)

    # ---- 액션 요약 (Switch 테이블용, 최대 폭 지정) ----
    def _action_summary(self, a, width):
        t = a.get("type", "none")
        if t == "none":
            s = "-"
        elif t == "effect":
            s = a.get("effect", "?")
        elif t == "channel_select":
            s = "%s:%s" % (a.get("effect", "?")[:5], chr(65 + a.get("channel", 0)))
        elif t == "scene":
            s = "Scene%d" % a.get("number", 1)
        elif t == "looper":
            b = a.get("button", 0)
            s = "Lp." + (LOOPER_BTN_NAMES[b] if b < len(LOOPER_BTN_NAMES) else "?")
        elif t == "tap_tempo":
            s = "TapTmp"
        elif t == "tuner":
            s = "Tuner"
        elif t == "preset_inc":
            s = "Preset+"
        elif t == "preset_dec":
            s = "Preset-"
        elif t == "page_inc":
            s = "Page+"
        elif t == "page_dec":
            s = "Page-"
        else:
            s = t
        return s[:width]

    def _hold_summary(self, btn, width):
        """Hold 열: 명시 액션이 없고 Press가 rotation이면 'Rot' 표기"""
        h = btn.get("hold", {})
        if h.get("type", "none") == "none":
            p = btn.get("press", {})
            if p.get("type") == "effect" and p.get("rotation"):
                return "Rot"
            return "-"
        return self._action_summary(h, width)

    # ---- 화면 그리기 ----
    @staticmethod
    def _set_xy(tg, x, y):
        if tg.x != x: tg.x = x
        if tg.y != y: tg.y = y

    @staticmethod
    def _set_hidden(tg, hidden):
        """hidden 대입은 값이 같아도 dirty 처리되어 전체 화면 재렌더를 유발 → 변경 시에만"""
        if tg.hidden != hidden:
            tg.hidden = hidden

    def _show_table_lines(self, show):
        self._set_hidden(self.tbl_hline, not show)
        for v in self.tbl_vlines:
            self._set_hidden(v, not show)

    EDIT_REDRAW_DEBOUNCE = 0.04  # 연속 회전 중에는 멈춘 뒤 한 번만 다시 그림

    def _update_edit_display(self):
        if not self.display_dirty:
            return
        # 회전 직후면 잠깐 대기 (다음 회전 이벤트가 이어지면 병합)
        if time.monotonic() < self.edit_redraw_at:
            return
        self.display_dirty = False
        s = self.edit_screen
        self._cursor_placed = False
        self._show_table_lines(s in (self.SCR_SWITCH, self.SCR_PARAM))
        if s == self.SCR_MAIN:
            self._draw_main()
        elif s == self.SCR_SWITCH:
            self._draw_switch_table()
        elif s == self.SCR_ACTION:
            self._draw_action()
        elif s == self.SCR_GLOBAL:
            self._draw_global()
        else:
            self._draw_param()
        if s != self.SCR_PARAM:
            for tg in self.swatches:
                self._set_hidden(tg, True)
            self._set_hidden(self.swatch_frame, True)
        if not self._cursor_placed:
            self._hide_cursor()
        self._show_group(self.grp_edit)
        self.display.refresh()

    # 화면별 레이아웃 (pitch, y0, scale)
    #  MENU  : MAIN / ACTION / GLOBAL — 항목 적음, scale 2, 넓은 간격
    #  PARAM : 최대 13행, scale 2, 촘촘
    #  TABLE : Switch 테이블 12행 — scale 1(6x8px)로 줄여 행간 확보 (헤더 구분선 겹침 방지)
    LAYOUT_MENU = (34, 44, 2)
    LAYOUT_PARAM = (17, 24, 2)
    # TABLE: 제목 줄 없이 y=6부터 헤더 + gap(8) + 10행, scale 2. 6 + 20*11 + 8 = 234 ≤ 240
    LAYOUT_TABLE = (20, 6, 2)
    ROW_H_MENU = 34    # 하위 호환 (swatch 계산 등)
    ROW_H_TABLE = 17

    # 테이블: 헤더 행과 데이터 행 사이 추가 여백 (밑줄이 들어갈 자리)
    TABLE_HEADER_GAP = 8

    def _set_layout(self, layout):
        """layout = (pitch, y0, scale[, header_gap]) — header_gap이 있으면 헤더 행 아래 여백"""
        if getattr(self, "_layout", None) == layout:
            return
        self._layout = layout
        pitch, y0, scale = layout[0], layout[1], layout[2]
        gap = layout[3] if len(layout) > 3 else (self.TABLE_HEADER_GAP if layout is self.LAYOUT_TABLE else 0)
        self._row_pitch = pitch
        self._row_y0 = y0
        self._header_gap = gap
        self._row_tops = []
        self._cursor_row = -1
        for r, lbl in enumerate(self.lbl_edit_rows):
            y = y0 + r * pitch + (gap if r >= 1 else 0)
            self._row_tops.append(y)
            # scale을 먼저 바꾸고 anchor를 재적용 (bounding box 변경 후 재계산되도록)
            if lbl.scale != scale:
                lbl.scale = scale
            lbl.anchored_position = (2, y)

    def _row_top(self, r):
        """행 r의 의도된 상단 y (레이아웃 기준, 라벨 상태와 무관)"""
        return self._row_tops[r]

    def _set_row_pitch(self, pitch):
        # 기존 호출 호환: pitch로 레이아웃 선택
        self._set_layout(self.LAYOUT_MENU if pitch == self.ROW_H_MENU else self.LAYOUT_PARAM)

    def _rows_clear(self, from_row=0):
        for r in range(from_row, self.EDIT_ROWS):
            self._set_label(self.lbl_edit_rows[r], "")

    def _row(self, r, text, selected=False, color=None):
        """행 텍스트 설정. 첫 글자가 커서 문자('>' 또는 '*')면 떼어내어 별도 커서 라벨로 표시.
        행 라벨의 텍스트는 커서와 무관하게 유지되므로 커서 이동 시 색상만 바뀜(작은 dirty)."""
        cursor_ch = None
        if text and text[0] in ">*":
            cursor_ch = text[0]
            text = " " + text[1:]
        lbl = self.lbl_edit_rows[r]
        if lbl.text != text:
            lbl.text = text
            lbl.anchored_position = (2, self._row_top(r))
        # 행 색상은 선택 여부와 무관하게 고정 — color 변경은 라벨 전체 글리프를 dirty로 만들어
        # 커서 이동마다 ~200ms를 소모함. 선택 강조는 커서 라벨('>')만으로 표현.
        c = color if color is not None else 0xDDDDDD
        if lbl.color != c:
            lbl.color = c
        if cursor_ch:
            self._place_cursor(r, cursor_ch, c if color is not None else 0x00FF00)

    def _place_cursor(self, r, ch, color):
        cur = self.lbl_edit_cursor
        if cur.text != ch:
            cur.text = ch
        if cur.color != color:
            cur.color = color
        if self._cursor_row != r or cur.scale != self.lbl_edit_rows[r].scale:
            cur.scale = self.lbl_edit_rows[r].scale
            cur.anchored_position = (2, self._row_top(r))
            self._cursor_row = r
        self._set_hidden(cur, False)
        self._cursor_placed = True

    def _hide_cursor(self):
        self._set_hidden(self.lbl_edit_cursor, True)
        self._cursor_row = -1

    def _draw_main(self):
        self._set_row_pitch(self.ROW_H_MENU)
        self._set_label(self.lbl_edit_title, "[EDIT]")
        pg = self.pages[self.edit_page]
        pg_txt = "P%d %s" % (self.edit_page + 1, pg.get("name", "")[:6])
        c = self.edit_cursor
        e = self.edit_editing_value
        # "Switch  P2 FX" — 페이지 선택 중(*)이면 노란색
        self._row(0, "%sSwitch %s" % ("*" if (c == 0 and e) else (">" if c == 0 else " "), pg_txt),
                  c == 0, 0xFFFF00 if (c == 0 and e) else None)
        # "Copy   P2 -> P3" — 대상 선택 중이면 노란색 (소스 = Switch 행의 페이지)
        if c == 1 and e:
            dst = self.pages[self.copy_dst]
            self._row(1, "*Copy P%d->P%d %s" % (self.edit_page + 1, self.copy_dst + 1, dst.get("name", "")[:5]),
                      True, 0xFFFF00)
        else:
            self._row(1, "%sCopy Page" % (">" if c == 1 else " "), c == 1)
        self._row(2, "%sGlobal Settings" % (">" if c == 2 else " "), c == 2)
        self._row(3, "%sExit" % (">" if c == 3 else " "), c == 3)
        self._rows_clear(4)

    def _draw_switch_table(self):
        self._set_layout(self.LAYOUT_TABLE)
        pg = self.pages[self.edit_page]
        self._set_label(self.lbl_edit_title, "")   # 제목 줄 없음 — 헤더 행에 페이지 표기
        # scale 2: 문자 폭 12px, 행 20자. 컬럼: [>SW](3) | [Press](10) | [Hold](7)
        # 헤더 행: 첫 컬럼에 페이지 번호(P1), 나머지 컬럼명. 제목 라벨은 비움(공간 없음)
        # 페이지 이름은 MAIN 화면에서 이미 확인 가능
        self._set_label(self.lbl_edit_title, "")
        hdr_sel = (self.edit_cursor == 0)
        e = hdr_sel and self.edit_editing_value
        if e:
            # 이름 편집 중: 커서 칸을 [ ]로 표시, 노란색
            chars = list(self.edit_name); pp = self.edit_name_pos
            shown = "".join(chars[:pp]) + "[" + chars[pp] + "]" + "".join(chars[pp + 1:])
            self._row(0, "*P%d %s" % (self.edit_page + 1, shown), True, 0xFFFF00)
        else:
            name = pg.get("name", "")[:self.PAGE_NAME_LEN]
            # 헤더: 페이지 번호+이름 (선택 가능) | Press | Hold
            hdr = "%sP%d %-6s Press Hold" % (">" if hdr_sel else " ", self.edit_page + 1, name)
            self._row(0, hdr[:20], hdr_sel, 0xCCCCCC if not hdr_sel else None)
        y0, h = self._row_y0, self._row_pitch
        # 헤더 밑줄: 헤더 행(y0..y0+h)과 1행 사이 gap의 중앙
        self._set_xy(self.tbl_hline, self.tbl_hline.x, y0 + h + self.TABLE_HEADER_GAP // 2 - 1)
        # 세로선: 컬럼 경계 — SW(3자) 뒤, Press(10자) 뒤. 글자와 붙지 않게 셀 여백 중앙에
        # 세로선은 헤더 밑줄 아래부터 (헤더 행은 페이지 이름이 컬럼을 가로지름)
        vy = y0 + h + self.TABLE_HEADER_GAP // 2
        self._set_xy(self.tbl_vlines[0], 2 + 12 * 3 - 4, vy)
        self._set_xy(self.tbl_vlines[1], 2 + 12 * 13 - 4, vy)
        btns = self._edit_buttons()
        for i in range(NUM_BUTTONS):
            b = btns[i]
            sel = (self.edit_cursor == i + 1)
            line = "%s%-2s %-9s %-6s" % (">" if sel else " ", self.BTN_ABBREV[i],
                                          self._action_summary(b.get("press", {}), 9),
                                          self._hold_summary(b, 6))
            self._row(1 + i, line, sel)
        self._rows_clear(1 + NUM_BUTTONS)

    def _draw_action(self):
        self._set_row_pitch(self.ROW_H_MENU)
        b = self._edit_buttons()[self.edit_btn_idx]
        self._set_label(self.lbl_edit_title, "P%d SW %s" % (self.edit_page + 1, self.BTN_ABBREV[self.edit_btn_idx]))
        items = ["Press", "Hold"]
        vals = [self._action_summary(b.get("press", {}), 11), self._hold_summary(b, 11)]
        for i, (it, v) in enumerate(zip(items, vals)):
            sel = (self.edit_cursor == i)
            self._row(i, "%s%-6s %s" % (">" if sel else " ", it, v), sel)
        self._rows_clear(len(items))

    def _draw_global(self):
        self._set_row_pitch(self.ROW_H_MENU)
        self._set_label(self.lbl_edit_title, "Global Settings")
        items = self._screen_items()
        for i, it in enumerate(items):
            sel = (self.edit_cursor == i)
            e = sel and self.edit_editing_value
            v = self._get_global_value(it)
            prefix = "*" if e else (">" if sel else " ")
            self._row(i, "%s%-9s%s" % (prefix, it, v), sel)
        self._rows_clear(len(items))

    def _get_global_value(self, item):
        if item == "HoldTime":
            return "%.1fs" % self.hold_time
        if item == "HoldAt":
            return "Release" if self.hold_mode == "release" else "Timeout"
        if item == "StartPg":
            if self.start_page < 0:
                return "Last"
            return "P%d %s" % (self.start_page + 1, self.pages[self.start_page].get("name", "")[:5])
        return ""

    def _param_layout(self, n_items):
        """항목 수에 따라 행 간격 자동 조절 — 적으면 넓게, 많으면 촘촘하게.
        헤더 1행 + n행이 y0=6부터 240 안에 들어가도록: 6 + pitch*(n+1) + gap ≤ 236"""
        gap = self.TABLE_HEADER_GAP
        pitch = (236 - 6 - gap) // (n_items + 1)
        pitch = max(20, min(34, pitch))
        return (pitch, 6, 2, gap)

    def _draw_param(self):
        # 테이블 레이아웃 (Switch 테이블과 동일): 헤더 행 + 밑줄 + 이름|값 세로선
        params = self._get_edit_params()
        self._set_layout(self._param_layout(len(params)))
        self._set_label(self.lbl_edit_title, "")
        press_label = "Press" if self.edit_press_idx == 0 else "Hold"
        # 헤더: "P2 B Press" (컨텍스트) — 값 컬럼 헤더는 생략
        self._row(0, "P%d %s %s" % (self.edit_page + 1, self.BTN_ABBREV[self.edit_btn_idx], press_label),
                  False, 0xCCCCCC)
        y0, h = self._row_y0, self._row_pitch
        self._set_xy(self.tbl_hline, self.tbl_hline.x, y0 + h + self.TABLE_HEADER_GAP // 2 - 1)
        # 이름 컬럼 7자(+prefix 1) → 세로선은 8자 뒤. 두 번째 세로선은 숨김
        self._set_xy(self.tbl_vlines[0], 2 + 12 * 8 - 4, y0 - 2)
        self._set_hidden(self.tbl_vlines[0], False)
        self._set_hidden(self.tbl_vlines[1], True)
        self._set_hidden(self.tbl_hline, False)

        # 최대 9항목 + 헤더 = 10행 → 스크롤 불필요하지만 안전하게 유지
        max_rows = self.EDIT_ROWS - 1
        start = max(0, self.edit_cursor - (max_rows - 1))
        for r in range(max_rows):
            mi = start + r
            if mi < len(params):
                item = params[mi]
                sel = (mi == self.edit_cursor)
                e = sel and self.edit_editing_value
                # 색상 항목은 텍스트 값 대신 견본(swatch)으로 표시
                is_color_item = (item == "Color" or item.startswith("Col."))
                val = "" if is_color_item else self._get_param_value(item)
                name = item
                if item == "Color" and e and self.edit_led_idx < 3:
                    name = "Col.%s" % self.LED_LABELS[self.edit_led_idx]  # Col.L1 등
                elif item.startswith("Col.") and item != "Col.OD" and e and self.edit_led_idx < 3:
                    name = "%s.%s" % (item, self.LED_LABELS[self.edit_led_idx])   # Col.A.L2 등 (7자)
                prefix = "*" if e else (">" if sel else " ")
                text = "%s%-7s %s" % (prefix, name[:7], val)
                self._row(1 + r, text[:20], sel)
            else:
                self._row(1 + r, "", False)
        self._draw_swatches(start)

    def _draw_swatches(self, start):
        colors = self._edit_preview_colors()
        if colors is None:
            for tg in self.swatches:
                self._set_hidden(tg, True)
            self._set_hidden(self.swatch_frame, True)
            return
        row = self.edit_cursor - start
        # 라벨의 실제 렌더링 중심(lbl.y)에 견본 세로 중앙을 맞춤.
        # (anchor 계산값과 실제 위치가 다르므로 lbl.y가 진실)
        lbl = self.lbl_edit_rows[1 + row]
        y = lbl.y - self.SWATCH_SZ // 2
        SW, GAP = self.SWATCH_SZ, 4
        a = self._cur_action()
        item = self._get_edit_params()[self.edit_cursor]
        is_ch = item.startswith("Col.") and item != "Col.OD"
        if is_ch:
            cc = a.get("ch_colors", [])
            spec = cc["ABCD".index(item[-1])] if "ABCD".index(item[-1]) < len(cc) else "OFF"
        else:
            spec = a.get("color")
        editing_led = ((item == "Color" or is_ch) and self.edit_editing_value and self.edit_led_idx < 3)
        per_led = ((item == "Color" or is_ch) and (isinstance(spec, list) or editing_led))
        n = 3 if per_led else 1
        # 견본은 값 컬럼 시작(세로선 오른쪽)에 왼쪽 정렬 — 텍스트 값과 겹치지 않음
        x0 = 2 + 12 * 8 + 4
        frame_shown = False
        for i in range(3):
            tg = self.swatches[i]
            if i < n:
                c = colors[i]
                self.swatch_pals[i][0] = (c[0] << 16) | (c[1] << 8) | c[2]
                nx, ny = x0 + i * (SW + GAP), y
                if tg.x != nx: tg.x = nx
                if tg.y != ny: tg.y = ny
                self._set_hidden(tg, False)
                if editing_led and i == self.edit_led_idx:
                    fx, fy = tg.x - 2, tg.y - 2
                    if self.swatch_frame.x != fx: self.swatch_frame.x = fx
                    if self.swatch_frame.y != fy: self.swatch_frame.y = fy
                    self._set_hidden(self.swatch_frame, False)
                    frame_shown = True
            else:
                self._set_hidden(tg, True)
        if not frame_shown:
            self._set_hidden(self.swatch_frame, True)

    def _get_param_value(self, item):
        a = self._cur_action()
        t = a.get("type", "none")
        if item == "Type":
            return t
        if item == "Target":
            if t in ("effect", "channel_select"):
                return a.get("effect", "?")
            if t == "scene":
                return str(a.get("number", "?"))
            if t == "looper":
                b = a.get("button", 0)
                return LOOPER_BTN_NAMES[b] if b < len(LOOPER_BTN_NAMES) else "?"
            return "-"
        if item == "Chan":
            return chr(65 + a.get("channel", 0))
        if item == "Rotate":
            return "ON" if a.get("rotation") else "OFF"
        if item == "Color":
            c = a.get("color", "OFF")
            if isinstance(c, list):
                if self.edit_editing_value and self.edit_led_idx < 3:
                    return PALETTE_ABBREV.get(c[self.edit_led_idx], "?")
                return "/".join(PALETTE_ABBREV.get(x, "?")[:2] for x in c[:3])
            return PALETTE_ABBREV.get(c, "?")
        if item == "Col.OD":
            return PALETTE_ABBREV.get(a.get("color2", "ORANGE"), "?")
        if item == "Chans":
            # 포함 채널은 대문자, 제외는 '.'; 편집 중이면 서브커서를 [ ]로 표시
            # 예: "A B C D" / "[A]B C D" / "A . C D"
            channels = a.get("channels", [0, 1, 2, 3])
            out = ""
            for i, letter in enumerate("ABCD"):
                s = letter if i in channels else "."
                if self.edit_editing_value and i == self.edit_ch_idx:
                    out += "[" + s + "]"
                else:
                    out += (" " if out and not out.endswith("]") else "") + s
            if self.edit_editing_value:
                # 5번째 위치 = Done (클릭으로 편집 종료)
                out += " [OK]" if self.edit_ch_idx == 4 else " OK"
            return out
        if item.startswith("Col."):
            ch = "ABCD".index(item[-1])
            cc = a.get("ch_colors", ["GREEN", "YELLOW", "ORANGE", "RED"])
            if ch >= len(cc):
                return "?"
            c = cc[ch]
            if isinstance(c, list):
                return "/".join(PALETTE_ABBREV.get(x, "?")[:2] for x in c[:3])
            return PALETTE_ABBREV.get(c, "?")
        return ""

    # --------------------------------------------------------
    # Welcome screen
    # --------------------------------------------------------
    def show_welcome(self):
        splash = displayio.Group()
        bg = displayio.Bitmap(240, 240, 1)
        p = displayio.Palette(1)
        p[0] = 0x000000
        splash.append(displayio.TileGrid(bg, pixel_shader=p))
        splash.append(label.Label(
            terminalio.FONT, text="MIDI Captain", color=0xFFFFFF, scale=3,
            anchor_point=(0.5, 0.5), anchored_position=(120, 100),
        ))
        splash.append(label.Label(
            terminalio.FONT, text="for FM3", color=0x00AAFF, scale=2,
            anchor_point=(0.5, 0.5), anchored_position=(120, 140),
        ))
        self.display.show(splash)
        self.display.refresh()

        # LED 테스트: 팔레트 12색 순회
        for i in range(NUM_BUTTONS):
            base = i * LEDS_PER_BUTTON
            color = pal(PALETTE_NAMES[i % 11])
            for j in range(LEDS_PER_BUTTON):
                self.leds.pixels[base + j] = color
            self.leds.pixels.show()
            time.sleep(0.15)
        time.sleep(0.5)
        for i in range(NUM_PIXELS):
            self.leds.pixels[i] = (0, 0, 0)
        self.leds.pixels.show()

    # --------------------------------------------------------
    # Polling
    # --------------------------------------------------------
    def _do_polling(self, now):
        if now - self.poll_timer < self.poll_interval:
            return
        self.poll_timer = now
        self.poll_count += 1
        self.send_get_scene()
        slot = self.poll_count % 3
        if slot == 0:
            self.send_status_dump()
        elif slot == 1:
            self.send_query_patch_name()
        else:
            self.send_query_scene_name()
            self.send_get_tempo()
            if self.looper_buttons:
                self.send_get_looper()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------
    def run(self):
        print("FM3 Controller Started")
        self.show_welcome()
        self.uart.reset_input_buffer()
        self.send_status_dump()
        self.send_get_scene()
        self.send_get_tempo()
        self.send_query_patch_name()
        self.send_query_scene_name()
        gc_count = 0

        while True:
            now = time.monotonic()

            self.process_encoder()
            self.process_buttons()
            self.process_midi_in()

            if not self.edit_mode:
                if self.page_save_at and now >= self.page_save_at:
                    self.page_save_at = 0
                    self._save_last_page()
                if self.name_query_at and now >= self.name_query_at:
                    self.name_query_at = 0
                    self.send_query_patch_name()
                self._do_polling(now)

                for i in self.tap_tempo_buttons:
                    self._update_tap_tempo_led(i)

                # tap 타입 순간 점등 만료 → 복귀
                for i in range(NUM_BUTTONS):
                    t = self.tap_flash_until[i]
                    if t and now >= t:
                        self.tap_flash_until[i] = 0
                        self._update_button_leds(i)

            # push 데이터를 받은 적이 있을 때만 타임아웃 적용
            # (FM3 "Send Realtime SysEx" off면 push가 없으므로 사용자 토글이 유일한 신호)
            if (self.tuner_active and self.tuner_last_data
                    and now - self.tuner_last_data > 5.0):
                self.tuner_active = False
                self.display_dirty = True

            self.leds.update()

            if self.edit_mode:
                self._update_edit_display()
            else:
                self._update_display()

            gc_count += 1
            if gc_count >= 1000:
                gc.collect()
                gc_count = 0


# ============================================================
if __name__ == "__main__":
    FM3Controller().run()
