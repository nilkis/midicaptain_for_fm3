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
# 버튼별 LED 점등 조건 마스크 (REC은 record|overdub, UNDO는 상태 없음 → 항상 점등)
LOOPER_LED_MASKS = (0x05, 0x02, 0x00, 0x08, 0x10, 0x20)

# Effect IDs
EFFECT_IDS = {
    "COMP1": 46, "COMP2": 47,
    "GRAPHEQ1": 50, "GRAPHEQ2": 51,
    "PARAEQ1": 54, "PARAEQ2": 55,
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

# Channel colors: green / yellow / orange / red
CHANNEL_COLORS = [(0, 255, 0), (255, 255, 0), (255, 128, 0), (255, 0, 0)]

OFF_W = 0.02  # OFF_COLOR brightness ratio

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# ============================================================
# Default Button Configuration
# ============================================================
def _btn(short, long_, c_short, c_long):
    return {"short": short, "long": long_,
            "color_short": list(c_short), "color_long": list(c_long)}


def _fx(name):
    return {"type": "effect", "effect": name}


def _looper(btn):
    return {"type": "looper", "button": btn}  # 0=REC 1=PLAY 2=UNDO 3=ONCE 4=REV 5=HALF


def default_pages():
    """호출 시마다 새 객체 생성 (공유 참조 방지)"""
    none_a = {"type": "none"}
    black = [0, 0, 0]
    # Page 1: effects block on/off
    fx_page = [
        _btn(_fx("COMP1"), dict(none_a), [255, 255, 0], black),
        _btn(_fx("DRIVE1"), {"type": "channel_rotation", "effect": "DRIVE1"},
             [255, 128, 0], black),
        _btn(_fx("CHORUS1"), dict(none_a), [0, 204, 204], black),
        _btn(_fx("DELAY1"), dict(none_a), [0, 0, 255], black),
        _btn({"type": "preset_inc"}, {"type": "preset_dec"}, [0, 0, 255], [0, 0, 255]),
        _btn(_fx("REVERB1"), dict(none_a), [128, 0, 255], black),
        _btn(_fx("FLANGER1"), dict(none_a), [255, 0, 255], black),
        _btn(_fx("PHASER1"), dict(none_a), [0, 255, 128], black),
        _btn(_fx("TREMOLO1"), dict(none_a), [0, 128, 255], black),
        _btn({"type": "tap_tempo"}, {"type": "tuner"}, [0, 0, 255], [0, 0, 255]),
    ]
    # Page 2: scene & preset change
    scene_page = [
        _btn({"type": "scene", "number": 1}, {"type": "scene", "number": 5},
             [255, 0, 0], [0, 255, 0]),
        _btn({"type": "scene", "number": 2}, {"type": "scene", "number": 6},
             [255, 0, 0], [0, 255, 0]),
        _btn({"type": "scene", "number": 3}, {"type": "scene", "number": 7},
             [255, 0, 0], [0, 255, 0]),
        _btn({"type": "scene", "number": 4}, {"type": "scene", "number": 8},
             [255, 0, 0], [0, 255, 0]),
        _btn({"type": "preset_inc"}, {"type": "preset_dec"}, [0, 0, 255], [0, 0, 255]),
        _btn({"type": "scene", "number": 5}, dict(none_a), [0, 255, 0], black),
        _btn({"type": "scene", "number": 6}, dict(none_a), [0, 255, 0], black),
        _btn({"type": "scene", "number": 7}, dict(none_a), [0, 255, 0], black),
        _btn({"type": "scene", "number": 8}, dict(none_a), [0, 255, 0], black),
        _btn({"type": "tap_tempo"}, {"type": "tuner"}, [0, 0, 255], [0, 0, 255]),
    ]
    # Page 3: looper (SysEx 0x0F)
    looper_page = [
        _btn(_looper(0), dict(none_a), [255, 0, 0], black),      # REC
        _btn(_looper(1), dict(none_a), [0, 255, 0], black),      # PLAY
        _btn(_looper(3), dict(none_a), [255, 255, 0], black),    # ONCE
        _btn(_looper(2), dict(none_a), [200, 200, 200], black),  # UNDO
        _btn({"type": "preset_inc"}, {"type": "preset_dec"}, [0, 0, 255], [0, 0, 255]),
        _btn(_looper(4), dict(none_a), [0, 204, 204], black),    # REV
        _btn(_looper(5), dict(none_a), [255, 128, 0], black),    # HALF
        _btn(_fx("LOOPER1"), dict(none_a), [128, 0, 255], black),  # looper block on/off
        _btn(dict(none_a), dict(none_a), black, black),
        _btn({"type": "tap_tempo"}, {"type": "tuner"}, [0, 0, 255], [0, 0, 255]),
    ]
    return {"pages": [
        {"name": "FX", "buttons": fx_page},
        {"name": "SCENE", "buttons": scene_page},
        {"name": "LOOPER", "buttons": looper_page},
    ]}


CONFIG_FILE = "config.json"


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        # v2: {"pages": [{"name":..., "buttons": [10개 버튼]}, ...]}
        if isinstance(cfg, dict):
            pages = cfg.get("pages")
            if (isinstance(pages, list) and pages and
                    all(isinstance(p, dict) and
                        isinstance(p.get("buttons"), list) and
                        len(p["buttons"]) == NUM_BUTTONS for p in pages)):
                return cfg
        # v1(단일 리스트) → v2 마이그레이션: 기존 설정을 page 1로 유지
        elif isinstance(cfg, list) and len(cfg) == NUM_BUTTONS:
            defaults = default_pages()
            return {"pages": [{"name": "MAIN", "buttons": cfg}] + defaults["pages"][1:]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return default_pages()


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError as e:
        print("Config save error:", e)


def color_off(c):
    return (int(c[0] * OFF_W), int(c[1] * OFF_W), int(c[2] * OFF_W))


def color_avg(c1, c2):
    return ((c1[0] + c2[0]) // 2, (c1[1] + c2[1]) // 2, (c1[2] + c2[2]) // 2)


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

    def bpm(self):
        return 60.0 / self.interval if self.interval > 0 else 120


# ============================================================
# LED Manager — 기존(code.py.bak)과 동일한 경량 구조
# ============================================================
class LEDManager:
    def __init__(self, pin, num_pixels):
        self.pixels = neopixel.NeoPixel(pin, num_pixels, brightness=0.5, auto_write=False)
        self.num_pixels = num_pixels
        self.num_buttons = num_pixels // LEDS_PER_BUTTON
        # Per-button: [LED1_color, LED2_color, LED3_color]
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
        # 변경이 없으면 pixels 재구성/전송 생략 (메인 루프 반응성)
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
    def __init__(self):
        self.full_config = load_config()
        self.pages = self.full_config["pages"]
        self.page_idx = 0
        self.config = self.pages[0]["buttons"]  # 현재 페이지의 버튼 10개
        self.long_press_time = 0.5  # seconds

        # MIDI setup
        self.uart = busio.UART(
            tx=board.GP16, rx=board.GP17,
            baudrate=31250, timeout=0.001,
            receiver_buffer_size=2048,  # display.refresh() 등 수백 ms 정지에도 유실 방지
        )
        self.midi = adafruit_midi.MIDI(
            midi_in=self.uart, midi_out=self.uart,
            out_channel=0, debug=False, in_buf_size=128
        )

        # Buttons
        self.keys = keypad.Keys(
            pins=BUTTON_PINS,
            value_when_pressed=False,
            pull=True,
            interval=0.02,
        )
        self.press_times = {}

        # Display
        displayio.release_displays()
        spi = busio.SPI(clock=DISPLAY_CLK, MOSI=DISPLAY_MOSI)
        display_bus = displayio.FourWire(
            spi, command=DISPLAY_DC, chip_select=DISPLAY_CS
        )
        self.display = ST7789(
            display_bus, width=240, height=240,
            rowstart=80, rotation=180, backlight_pin=DISPLAY_BL,
        )
        self.display.auto_refresh = False

        # LEDs
        self.leds = LEDManager(NEOPIXEL_PIN, NUM_PIXELS)
        self.tap = TapTempo()

        # Rotary encoder
        self.encoder = rotaryio.IncrementalEncoder(ENCODER_A, ENCODER_B)
        self.encoder_sw = digitalio.DigitalInOut(ENCODER_SW)
        self.encoder_sw.direction = digitalio.Direction.INPUT
        self.encoder_sw.pull = digitalio.Pull.UP
        self.encoder_last_pos = self.encoder.position
        self.encoder_sw_pressed = False
        self.encoder_sw_press_time = 0

        # FM3 state
        self.fx_states = {}       # fx_id → bypassed (bool)
        self.fx_channels = {}     # fx_id → current channel (int)
        self.fx_num_channels = {} # fx_id → supported channel count
        self.current_scene = None
        self.patch_name = ""
        self.patch_number = None
        self.scene_name = ""
        self.tempo_bpm = 120
        self.looper_state = 0   # SysEx 0x0F 비트마스크
        self.name_query_at = 0  # preset 변경 직후 이름 조회 예약 시각
        self._dump_sig = None   # status_dump 블록 구성 시그니처 (preset 변경 감지용)
        self.rx_buf = b""       # 자체 SysEx 프레임 파서 수신 버퍼

        # Polling — round-robin, 한 루프에 하나만 전송
        self.poll_timer = 0
        self.poll_interval = 0.15
        self.poll_slot = 0   # 0=status_dump, 그외 때때로 scene/patch/scene_name
        self.poll_count = 0  # status_dump 횟수 카운터

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

        # Display refresh control (auto_refresh=False이므로 수동 제어)
        self.display_refresh_timer = 0
        self.display_refresh_interval = 0.3

        # Edit mode
        self.edit_mode = False
        self.edit_level = 0       # 0=button select, 1=press type, 2=param edit
        self.edit_btn_idx = 0
        self.edit_press_idx = 0   # 0=short, 1=long (confirmed when entering level 2)
        self.edit_menu_idx = 0
        self.edit_editing_value = False
        self.edit_color_comp = 0  # 색상 편집 중 활성 컴포넌트: 0=R, 1=G, 2=B

        # Build lookup tables
        self._build_lookups()

        # Pre-build display groups (한 번만 생성, text만 업데이트)
        self._init_display_groups()

    def _build_lookups(self):
        self.fx_to_btn = {}       # fx_id → list of button indices
        self.scene_buttons = []   # button indices with scene type
        self.tap_buttons = []     # button indices with tap_tempo type
        self.looper_buttons = []  # button indices with looper type
        for i, cfg in enumerate(self.config):
            for press in ("short", "long"):
                action = cfg.get(press, {})
                atype = action.get("type", "none")
                if atype == "effect":
                    fx_name = action.get("effect", "")
                    fx_id = EFFECT_IDS.get(fx_name)
                    if fx_id is not None:
                        if fx_id not in self.fx_to_btn:
                            self.fx_to_btn[fx_id] = []
                        if i not in self.fx_to_btn[fx_id]:
                            self.fx_to_btn[fx_id].append(i)
                elif atype == "scene":
                    if i not in self.scene_buttons:
                        self.scene_buttons.append(i)
                elif atype == "tap_tempo":
                    if i not in self.tap_buttons:
                        self.tap_buttons.append(i)
                elif atype == "looper":
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
        self._show_temp(page.get("name", ""), "PAGE %d" % (self.page_idx + 1))

    # --------------------------------------------------------
    # Display 초기화 — 객체를 한 번만 생성
    # --------------------------------------------------------
    def _init_display_groups(self):
        # --- Normal screen (unified: page/bpm + patch + scene + action) ---
        self.grp_normal = displayio.Group()
        bg = displayio.Bitmap(240, 240, 1)
        pal = displayio.Palette(1)
        pal[0] = 0x000000
        self.grp_normal.append(displayio.TileGrid(bg, pixel_shader=pal))

        # (1) Page number — top left, medium font
        self.lbl_page = label.Label(
            terminalio.FONT, text="#1",
            color=0x888888, scale=2,
            anchor_point=(0.0, 0.0), anchored_position=(5, 5),
        )
        self.grp_normal.append(self.lbl_page)

        # (1) BPM — top right, medium font
        self.lbl_bpm = label.Label(
            terminalio.FONT, text="BPM:120",
            color=0x888888, scale=2,
            anchor_point=(1.0, 0.0), anchored_position=(235, 5),
        )
        self.grp_normal.append(self.lbl_bpm)

        # Separator line between (1) and (2)
        sep_bmp = displayio.Bitmap(220, 1, 1)
        sep_pal = displayio.Palette(1)
        sep_pal[0] = 0x444444
        self.grp_normal.append(displayio.TileGrid(sep_bmp, pixel_shader=sep_pal, x=10, y=28))

        # (2) Patch Name — medium font
        self.lbl_patch = label.Label(
            terminalio.FONT, text="---",
            color=0xFFFFFF, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 35),
        )
        self.grp_normal.append(self.lbl_patch)

        # (3) Scene Name — medium font, no "Scene:" prefix
        self.lbl_scene = label.Label(
            terminalio.FONT, text="---",
            color=0x00AAFF, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 75),
        )
        self.grp_normal.append(self.lbl_scene)

        # (4) Action name — largest font (e.g. "DRIVE1")
        self.lbl_action_name = label.Label(
            terminalio.FONT, text="",
            color=0xFFFF00, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 125),
        )
        self.grp_normal.append(self.lbl_action_name)

        # (4) Action state — largest font (e.g. "ON" / "OFF")
        self.lbl_action_state = label.Label(
            terminalio.FONT, text="",
            color=0xFFFF00, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 175),
        )
        self.grp_normal.append(self.lbl_action_state)

        # --- Tuner screen ---
        self.grp_tuner = displayio.Group()
        bg3 = displayio.Bitmap(240, 240, 1)
        pal3 = displayio.Palette(1)
        pal3[0] = 0x000000
        self.grp_tuner.append(displayio.TileGrid(bg3, pixel_shader=pal3))

        lbl_tuner_title = label.Label(
            terminalio.FONT, text="TUNER",
            color=0xFFFFFF, scale=3,
            anchor_point=(0.5, 0.0), anchored_position=(120, 20),
        )
        self.grp_tuner.append(lbl_tuner_title)

        self.lbl_tuner_info = label.Label(
            terminalio.FONT, text="Note: -  Str: -",
            color=0x00FFFF, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 90),
        )
        self.grp_tuner.append(self.lbl_tuner_info)

        self.lbl_tuner_cents = label.Label(
            terminalio.FONT, text="---",
            color=0xFFFF00, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 160),
        )
        self.grp_tuner.append(self.lbl_tuner_cents)

        # --- Edit screen ---
        self.grp_edit = displayio.Group()
        bg4 = displayio.Bitmap(240, 240, 1)
        pal4 = displayio.Palette(1)
        pal4[0] = 0x000000
        self.grp_edit.append(displayio.TileGrid(bg4, pixel_shader=pal4))

        self.lbl_edit_title = label.Label(
            terminalio.FONT, text="[EDIT]",
            color=0xFFFF00, scale=2,
            anchor_point=(0.5, 0.0), anchored_position=(120, 5),
        )
        self.grp_edit.append(self.lbl_edit_title)

        self.lbl_edit_lines = []
        for row in range(5):
            l = label.Label(
                terminalio.FONT, text="",
                color=0xAAAAAA, scale=2,
                anchor_point=(0.0, 0.0), anchored_position=(5, 32 + row * 38),
            )
            self.grp_edit.append(l)
            self.lbl_edit_lines.append(l)

        # Level 0 그리드 오른쪽 열 — 독립 색상 제어용
        self.lbl_edit_grid_r = []
        for row in range(5):
            l = label.Label(
                terminalio.FONT, text="",
                color=0x888888, scale=2,
                anchor_point=(0.0, 0.0), anchored_position=(125, 32 + row * 38),
            )
            self.grp_edit.append(l)
            self.lbl_edit_grid_r.append(l)

        # Track which group is currently shown
        self._current_group = None

    def _show_group(self, grp):
        if self._current_group is not grp:
            self.display.show(grp)
            self._current_group = grp

    # --------------------------------------------------------
    # SysEx helpers
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

    # --------------------------------------------------------
    # SysEx send commands
    # --------------------------------------------------------
    def send_status_dump(self):
        self._send_sysex([FM3_MODEL_ID, STATUS_DUMP])

    def send_tap_tempo(self):
        self._send_sysex([FM3_MODEL_ID, TAP_TEMPO_FUNC])
        self.tap.on_tap()
        # FM3 실제 BPM 즉시 조회
        self.send_get_tempo()

    def send_tuner(self, on):
        self._send_sysex([FM3_MODEL_ID, TUNER_FUNC, 1 if on else 0])

    def send_set_scene(self, scene_num):
        self._send_sysex([FM3_MODEL_ID, SET_SCENE, scene_num])

    def send_get_scene(self):
        self._send_sysex([FM3_MODEL_ID, SET_SCENE, 0x7F])

    def send_set_fx_status(self, fx_id, value):
        b1 = int(fx_id) % 128
        b2 = int(fx_id) // 128
        self._send_sysex([FM3_MODEL_ID, SET_FX_STATUS, b1, b2, value])

    def send_set_channel(self, fx_id, channel):
        b1 = int(fx_id) % 128
        b2 = int(fx_id) // 128
        self._send_sysex([FM3_MODEL_ID, SET_CHANNEL, b1, b2, channel])

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
    # MIDI receive processing
    # --------------------------------------------------------
    def process_midi_in(self):
        # 자체 SysEx 프레임 파서 — adafruit_midi.receive()는 in_buf보다 큰
        # SysEx(status_dump 등)를 완성하지 못해 뒤따르는 응답까지 유실시킴.
        # UART에서 직접 읽어 F0...F7 프레임을 추출한다 (송신은 adafruit_midi 유지).
        chunk = self.uart.read(256)
        if chunk:
            self.rx_buf += chunk
        buf = self.rx_buf
        if not buf:
            return
        while True:
            start = buf.find(b'\xf0')
            if start < 0:
                buf = b''  # sysex 시작 없음 → realtime 등 잔여 바이트 폐기
                break
            end = buf.find(b'\xf7', start + 1)
            if end < 0:
                if start > 0:
                    buf = buf[start:]  # 프레임 앞 잡음 제거, 완성 대기
                if len(buf) > 1024:
                    buf = b''  # F7 유실로 비정상 성장 시 리셋
                break
            frame = buf[start + 1:end]  # F0/F7 제외
            buf = buf[end + 1:]
            self._handle_frame(frame)
        self.rx_buf = buf

    def _handle_frame(self, frame):
        # frame: mfr_id(3) + model + func + payload... + checksum
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
            # 매 박자 push → LED 동기화만. display_dirty 금지
            # (매 박자 refresh는 루프를 세워 SysEx 응답 유실을 유발)
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
                    # scene 변경 감지 → 이름 즉시 조회
                    self.send_query_scene_name()
                    self.display_dirty = True
            return

        if func == QUERY_PATCH_NAME:
            if len(data) >= 36:
                num = data[2] + data[3] * 128
                name_bytes = data[4:36]
                name = "".join(chr(b) for b in name_bytes if 32 <= b < 127).strip()
                if name != self.patch_name:
                    self.patch_name = name
                    self.display_dirty = True
                if num != self.patch_number:
                    self.patch_number = num
                    # preset 변경 감지 → scene 이름/번호도 즉시 갱신
                    self.send_get_scene()
                    self.send_query_scene_name()
            return

        if func == QUERY_SCENE_NAME:
            if len(data) >= 35:
                name_bytes = data[3:35]
                name = "".join(chr(b) for b in name_bytes if 32 <= b < 127).strip()
                if name != self.scene_name:
                    self.scene_name = name
                    self.display_dirty = True
            return

        if func == TUNER_FUNC:
            payload_len = len(data) - 2
            if payload_len >= 3:
                self.tuner_note = data[2]
                self.tuner_string = data[3]
                self.tuner_cents = data[4]
                self.tuner_active = True
                self.tuner_last_data = time.monotonic()
                self.display_dirty = True
            elif payload_len >= 1:
                if data[2] == 0:
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
                id_lo = data[2]
                id_hi = data[3]
                fx_id = id_lo + 128 * id_hi
                channel = data[4]
                self.fx_channels[fx_id] = channel
                self._update_channel_leds(fx_id)
            return

    def _parse_status_dump(self, data):
        if len(data) < 4 or data[1] != STATUS_DUMP:
            return
        # 새 프리셋의 덤프 — 이전 상태를 모두 지우고 다시 채운다
        self.fx_states.clear()
        self.fx_channels.clear()
        self.fx_num_channels.clear()
        packets = data[2:-1]
        for i in range(0, len(packets) - 2, 3):
            id_lo = packets[i]
            id_hi = packets[i + 1]
            dd = packets[i + 2]
            fx_id = int(id_lo) + 128 * int(id_hi)
            bypassed = bool(dd & 0x01)
            channel = (dd >> 1) & 0x07
            num_ch = (dd >> 4) & 0x07
            self.fx_states[fx_id] = bypassed
            self.fx_channels[fx_id] = channel
            self.fx_num_channels[fx_id] = num_ch
        # 프리셋이 바뀌었으므로 모든 버튼 LED 일괄 갱신
        self._update_all_button_leds()
        # 블록 구성 변화 = preset 변경 신호 → 이름 즉시 조회 예약
        # (0.6초 폴링 로테이션보다 훨씬 빠른 0.15초 주기 감지)
        sig = len(self.fx_states) * 100000 + sum(self.fx_states)
        if sig != self._dump_sig:
            if self._dump_sig is not None and not self.name_query_at:
                self.name_query_at = time.monotonic() + 0.3
            self._dump_sig = sig

    # --------------------------------------------------------
    # Button handling
    # --------------------------------------------------------
    def process_buttons(self):
        while event := self.keys.events.get():
            key = event.key_number
            if event.pressed:
                self.press_times[key] = time.monotonic()
                # Tap tempo fires on press immediately
                cfg = self.config[key]
                if cfg.get("short", {}).get("type") == "tap_tempo":
                    self.send_tap_tempo()
                    self._show_temp("BPM", "%d" % self.tempo_bpm)
            elif event.released:
                press_time = self.press_times.pop(key, 0)
                duration = time.monotonic() - press_time
                if duration >= self.long_press_time:
                    self._handle_action(key, "long")
                else:
                    self._handle_action(key, "short")

    def _handle_action(self, idx, press_type):
        cfg = self.config[idx]
        action = cfg.get(press_type, {})
        atype = action.get("type", "none")

        if atype == "none":
            return

        if atype == "tap_tempo":
            return

        if atype == "scene":
            scene_num = action.get("number", 1) - 1
            self.send_set_scene(scene_num)
            self.current_scene = scene_num
            self._update_scene_leds()
            color = tuple(cfg.get("color_" + press_type, [255, 255, 255]))
            self.leds.set_button_color(idx, color)
            self._show_temp("SCENE", "%d" % action.get("number", 1))

        elif atype == "effect":
            fx_name = action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is None:
                return
            was_bypassed = self.fx_states.get(fx_id, True)
            new_value = 0 if was_bypassed else 1
            self.send_set_fx_status(fx_id, new_value)
            self.fx_states[fx_id] = not was_bypassed
            self._update_button_leds(idx)
            state_str = "ON" if was_bypassed else "OFF"
            self._show_temp(fx_name, state_str)

        elif atype == "channel_rotation":
            fx_name = action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is None:
                return
            cur_ch = self.fx_channels.get(fx_id, 0)
            available = sorted(action.get("channels", [0, 1, 2, 3]))
            if not available:
                available = [0, 1, 2, 3]
            try:
                idx = available.index(cur_ch)
            except ValueError:
                idx = -1
            new_ch = available[(idx + 1) % len(available)]
            self.send_set_channel(fx_id, new_ch)
            self.fx_channels[fx_id] = new_ch
            self._update_channel_leds(fx_id)
            self._show_temp(fx_name, "CH.%s" % chr(65 + new_ch))

        elif atype == "looper":
            btn = action.get("button", 0)
            self.send_looper_button(btn)
            self.send_get_looper()  # 새 상태 즉시 조회 → LED 갱신
            name = LOOPER_BTN_NAMES[btn] if btn < len(LOOPER_BTN_NAMES) else "?"
            self._show_temp("LOOPER", name)

        elif atype == "tuner":
            self.tuner_active = not self.tuner_active
            self.send_tuner(self.tuner_active)
            self._show_temp("TUNER", "ON" if self.tuner_active else "OFF")

        elif atype == "preset_inc":
            self.send_patch_inc()
            self._show_temp("PRESET", "+")
            self._blink_button(idx)
            # FM3 프리셋 로딩 후 이름 즉시 조회 예약
            self.name_query_at = time.monotonic() + 0.4

        elif atype == "preset_dec":
            self.send_patch_dec()
            self._show_temp("PRESET", "-")
            self._blink_button(idx)
            self.name_query_at = time.monotonic() + 0.4

    def _blink_button(self, idx):
        cfg = self.config[idx]
        color = tuple(cfg.get("color_short", [255, 255, 255]))
        self.leds.set_button_color(idx, color)

    # --------------------------------------------------------
    # LED update logic
    # --------------------------------------------------------
    def _update_all_button_leds(self):
        for i in range(NUM_BUTTONS):
            self._update_button_leds(i)

    def _update_button_leds(self, idx):
        cfg = self.config[idx]
        short_action = cfg.get("short", {})
        long_action = cfg.get("long", {})
        s_type = short_action.get("type", "none")
        l_type = long_action.get("type", "none")

        # Tap tempo: handled separately in main loop
        if s_type == "tap_tempo":
            return

        # Effect toggle + channel_rotation combo
        if s_type == "effect" and l_type == "channel_rotation":
            fx_name = short_action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is not None:
                if fx_id not in self.fx_states:
                    # 현재 프리셋에 없는 effect → 완전히 꺼짐
                    self.leds.set_button_color(idx, (0, 0, 0))
                    return
                ch = self.fx_channels.get(fx_id, 0)
                ch_colors_raw = long_action.get("ch_colors", None)
                if ch_colors_raw and ch < len(ch_colors_raw):
                    ch_color = tuple(ch_colors_raw[ch])
                else:
                    ch_color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]
                bypassed = self.fx_states[fx_id]
                color = ch_color if not bypassed else color_off(ch_color)
                self.leds.set_button_color(idx, color)
                return

        # Two Type A (short=A1, long=A2)
        if s_type in ("effect", "scene", "looper") and l_type in ("effect", "scene", "looper"):
            c1 = self._get_action_color(idx, "short")
            c2 = self._get_action_color(idx, "long")
            avg = color_avg(c1, c2)
            self.leds.set_button_leds(idx, c1, c2, avg)
            return

        # Single action
        if s_type in ("effect", "scene", "looper") and l_type == "none":
            color = self._get_action_color(idx, "short")
            self.leds.set_button_color(idx, color)
            return

        # Type A + Type C
        if s_type in ("effect", "scene", "looper") and l_type in ("tuner", "preset_inc", "preset_dec"):
            color = self._get_action_color(idx, "short")
            self.leds.set_button_color(idx, color)
            return

        # Type C + Type A
        if s_type in ("tuner", "preset_inc", "preset_dec") and l_type in ("effect", "scene", "looper"):
            color = self._get_action_color(idx, "long")
            self.leds.set_button_color(idx, color)
            return

        # Default: off
        self.leds.set_button_color(idx, (0, 0, 0))

    def _get_action_color(self, idx, press_type):
        cfg = self.config[idx]
        action = cfg.get(press_type, {})
        atype = action.get("type", "none")
        color = tuple(cfg.get("color_" + press_type, [0, 0, 0]))

        if atype == "effect":
            fx_name = action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is not None:
                if fx_id not in self.fx_states:
                    # 현재 프리셋에 없는 effect → 완전히 꺼짐
                    return (0, 0, 0)
                bypassed = self.fx_states[fx_id]
                return color if not bypassed else color_off(color)
            return color_off(color)

        if atype == "scene":
            scene_num = action.get("number", 1) - 1
            if self.current_scene == scene_num:
                return color
            return color_off(color)

        if atype == "looper":
            btn = action.get("button", 0)
            mask = LOOPER_LED_MASKS[btn] if btn < len(LOOPER_LED_MASKS) else 0
            if mask == 0:
                return color  # UNDO 등 상태 없는 버튼은 항상 점등
            return color if (self.looper_state & mask) else color_off(color)

        return color_off(color)

    def _update_scene_leds(self):
        for btn_idx in self.scene_buttons:
            self._update_button_leds(btn_idx)

    def _update_channel_leds(self, fx_id):
        if fx_id in self.fx_to_btn:
            for btn_idx in self.fx_to_btn[fx_id]:
                self._update_button_leds(btn_idx)

    def _update_tap_led(self, idx):
        cfg = self.config[idx]
        c_short = tuple(cfg.get("color_short", [0, 0, 255]))
        l_type = cfg.get("long", {}).get("type", "none")

        flashing = self.tap.is_flashing()

        if l_type in ("effect", "scene"):
            if flashing:
                self.leds.set_button_color(idx, c_short)
            else:
                a_color = self._get_action_color(idx, "long")
                self.leds.set_button_color(idx, a_color)
        else:
            if flashing:
                self.leds.set_button_color(idx, c_short)
            else:
                self.leds.set_button_color(idx, color_off(c_short))

    # --------------------------------------------------------
    # Display — label.text만 변경, display.show()는 group 전환시만
    # --------------------------------------------------------
    def _show_temp(self, name, state="", duration=1.5):
        self.display_temp_name = name
        self.display_temp_state = state
        self.display_temp_until = time.monotonic() + duration
        self.display_dirty = True

    def _set_label(self, lbl, text):
        """label.text를 값이 변경된 경우만 설정 (displayio 내부 처리 방지)"""
        if lbl.text != text:
            lbl.text = text

    def _update_display(self):
        now = time.monotonic()

        # Tuner 화면
        if self.tuner_active and now - self.tuner_last_data < 3.0:
            if self.display_dirty:
                self.display_dirty = False
                note = NOTE_NAMES[self.tuner_note % 12]
                self._set_label(self.lbl_tuner_info, "Note: %s  Str: %d" % (note, self.tuner_string + 1))
                cents = self.tuner_cents - 63
                if cents == 0:
                    self._set_label(self.lbl_tuner_cents, "IN TUNE")
                    self.lbl_tuner_cents.color = 0x00FF00
                else:
                    self._set_label(self.lbl_tuner_cents, "%+d cents" % cents)
                    self.lbl_tuner_cents.color = 0xFFFF00
                self._show_group(self.grp_tuner)
                self.display.refresh()
            return

        # Temp expired → action 영역 클리어
        if self.display_temp_name and now >= self.display_temp_until:
            self.display_temp_name = None
            self.display_temp_state = ""
            self._update_all_button_leds()
            self.display_dirty = True

        # Normal display (통합 레이아웃)
        if self.display_dirty:
            self.display_dirty = False
            # (1) Page — "P1 FX"
            page_name = self.pages[self.page_idx].get("name", "")[:6]
            self._set_label(self.lbl_page, "P%d %s" % (self.page_idx + 1, page_name))
            # (1) BPM
            self._set_label(self.lbl_bpm, "BPM:%d" % self.tempo_bpm)
            # (2) Patch Name
            self._set_label(self.lbl_patch, self.patch_name if self.patch_name else "---")
            # (3) Scene Name
            self._set_label(self.lbl_scene, self.scene_name if self.scene_name else "---")
            # (4) Action
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

    def _refresh_display(self, now):
        """수동 display refresh — throttled"""
        if now - self.display_refresh_timer >= self.display_refresh_interval:
            self.display_refresh_timer = now
            self.display.refresh()

    # --------------------------------------------------------
    # Edit mode (Rotary Encoder)
    # --------------------------------------------------------
    EDIT_TYPES = ["effect", "scene", "tap_tempo", "tuner", "looper",
                  "preset_inc", "preset_dec", "channel_rotation", "none"]
    EFFECT_LIST = sorted(EFFECT_IDS.keys())
    EDIT_PARAMS = ["Type", "Target", "Color1", "Color2", "Color3", "Back"]
    BTN_ABBREV  = ("sw1", "sw2", "sw3", "sw4", "swUp", "swA", "swB", "swC", "swD", "swDn")

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
                    self._exit_edit_mode()
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
            else:
                self._change_page(delta)

    def _enter_edit_mode(self):
        self.edit_mode = True
        self.edit_level = 0
        self.edit_btn_idx = 0
        self.edit_press_idx = 0
        self.edit_menu_idx = 0
        self.edit_editing_value = False
        self.edit_color_comp = 0
        self.display_dirty = True

    def _exit_edit_mode(self):
        self.edit_mode = False
        save_config(self.full_config)
        self._build_lookups()
        self._update_all_button_leds()
        self.display_dirty = True

    def _edit_click(self):
        if self.edit_level == 0:
            # 버튼 선택 → Short/Long 선택 화면으로
            self.edit_level = 1
            self.edit_menu_idx = 0
        elif self.edit_level == 1:
            # Short(0) / Long(1) / Back(2)
            if self.edit_menu_idx == 2:   # Back → 버튼 선택으로 복귀
                self.edit_level = 0
            else:
                self.edit_press_idx = self.edit_menu_idx
                self.edit_level = 2
                self.edit_menu_idx = 0
                self.edit_editing_value = False
        elif self.edit_level == 2:
            press_type = "short" if self.edit_press_idx == 0 else "long"
            params = self._get_edit_params(self.config[self.edit_btn_idx], press_type)
            item = params[self.edit_menu_idx]
            if item == "Back":
                self.edit_level = 1
                self.edit_menu_idx = self.edit_press_idx
                self.edit_editing_value = False
                self.edit_color_comp = 0
            elif item in ("Color", "Col.A", "Col.B", "Col.C", "Col.D"):
                if not self.edit_editing_value:
                    self.edit_editing_value = True
                    self.edit_color_comp = 0   # R부터 시작
                else:
                    self.edit_color_comp += 1  # R→G→B→종료
                    if self.edit_color_comp > 2:
                        self.edit_editing_value = False
                        self.edit_color_comp = 0
            else:
                self.edit_editing_value = not self.edit_editing_value
        self.display_dirty = True

    def _edit_rotate(self, delta):
        if self.edit_level == 0:
            self.edit_btn_idx = (self.edit_btn_idx + delta) % NUM_BUTTONS
        elif self.edit_level == 1:
            self.edit_menu_idx = (self.edit_menu_idx + delta) % 3  # Short / Long / Back
        elif self.edit_level == 2:
            if self.edit_editing_value:
                self._edit_change_value(delta)
            else:
                press_type = "short" if self.edit_press_idx == 0 else "long"
                params = self._get_edit_params(self.config[self.edit_btn_idx], press_type)
                self.edit_menu_idx = (self.edit_menu_idx + delta) % len(params)
        self.display_dirty = True

    def _get_edit_params(self, cfg, press_type):
        atype = cfg.get(press_type, {}).get("type", "none")
        if atype == "channel_rotation":
            return ["Type", "Target",
                    "Ch.A", "Ch.B", "Ch.C", "Ch.D",
                    "Col.A", "Col.B", "Col.C", "Col.D",
                    "Back"]
        else:
            return ["Type", "Target", "Color", "Back"]

    def _edit_change_value(self, delta):
        press_type = "short" if self.edit_press_idx == 0 else "long"
        cfg = self.config[self.edit_btn_idx]
        params = self._get_edit_params(cfg, press_type)
        item = params[self.edit_menu_idx]

        if item == "Type":
            cur = cfg.get(press_type, {}).get("type", "none")
            i = self.EDIT_TYPES.index(cur) if cur in self.EDIT_TYPES else 0
            i = (i + delta) % len(self.EDIT_TYPES)
            cfg.setdefault(press_type, {})["type"] = self.EDIT_TYPES[i]

        elif item == "Target":
            self._edit_target(cfg, press_type, delta)

        elif item == "Color":
            c = list(cfg.get("color_" + press_type, [0, 0, 0]))
            c[self.edit_color_comp] = (c[self.edit_color_comp] + delta * 5) % 256
            cfg["color_" + press_type] = c
            self._update_button_leds(self.edit_btn_idx)

        elif item.startswith("Ch."):
            ch = "ABCD".index(item[-1])
            action = cfg.setdefault(press_type, {})
            channels = list(action.get("channels", [0, 1, 2, 3]))
            if delta > 0:
                if ch not in channels:
                    channels.append(ch)
            else:
                if ch in channels and len(channels) > 1:  # 최소 1채널 유지
                    channels.remove(ch)
            action["channels"] = sorted(channels)

        elif item.startswith("Col."):
            ch = "ABCD".index(item[-1])
            action = cfg.setdefault(press_type, {})
            raw = action.get("ch_colors", [list(c) for c in CHANNEL_COLORS])
            ch_colors = [list(c) for c in raw]
            while len(ch_colors) <= ch:
                ch_colors.append([0, 0, 0])
            ch_colors[ch][self.edit_color_comp] = (ch_colors[ch][self.edit_color_comp] + delta * 5) % 256
            action["ch_colors"] = ch_colors
            self._update_button_leds(self.edit_btn_idx)

    def _edit_target(self, cfg, press_type, delta):
        action = cfg.get(press_type, {})
        atype = action.get("type", "none")
        if atype in ("effect", "channel_rotation"):
            cur = action.get("effect", "")
            i = self.EFFECT_LIST.index(cur) if cur in self.EFFECT_LIST else 0
            i = (i + delta) % len(self.EFFECT_LIST)
            action["effect"] = self.EFFECT_LIST[i]
        elif atype == "scene":
            cur = action.get("number", 1)
            action["number"] = max(1, min(8, cur + delta))
        elif atype == "looper":
            cur = action.get("button", 0)
            action["button"] = (cur + delta) % len(LOOPER_BTN_NAMES)

    def _update_edit_display(self):
        if not self.display_dirty:
            return
        self.display_dirty = False

        if self.edit_level == 0:
            self._draw_edit_level0()
        elif self.edit_level == 1:
            self._draw_edit_level1()
        elif self.edit_level == 2:
            self._draw_edit_level2()

        self._show_group(self.grp_edit)
        self.display.refresh()

    def _draw_edit_level0(self):
        # 5행 2열 그리드: 왼열(sw1~swUp) / 오른열(swA~swDn) — 셀 독립 하이라이트
        self._set_label(self.lbl_edit_title, "[EDIT P%d] Sel SW" % (self.page_idx + 1))
        for row in range(5):
            li, ri = row, row + 5
            l_sel = (self.edit_btn_idx == li)
            r_sel = (self.edit_btn_idx == ri)
            self._set_label(self.lbl_edit_lines[row],
                            "%s %s" % (">" if l_sel else " ", self.BTN_ABBREV[li]))
            self.lbl_edit_lines[row].color = 0x00FF00 if l_sel else 0x888888
            self._set_label(self.lbl_edit_grid_r[row],
                            "%s %s" % (">" if r_sel else " ", self.BTN_ABBREV[ri]))
            self.lbl_edit_grid_r[row].color = 0x00FF00 if r_sel else 0x888888

    def _draw_edit_level1(self):
        # Short Press / Long Press / Back
        sw_name = BUTTON_NAMES[self.edit_btn_idx]
        self._set_label(self.lbl_edit_title, "[EDIT] %s" % sw_name)
        items = ["Short Press", "Long Press", "Back"]
        for i, item in enumerate(items):
            prefix = ">" if self.edit_menu_idx == i else " "
            self._set_label(self.lbl_edit_lines[i], "%s %s" % (prefix, item))
            self.lbl_edit_lines[i].color = 0x00FF00 if self.edit_menu_idx == i else 0xAAAAAA
        for i in range(3, 5):
            self._set_label(self.lbl_edit_lines[i], "")
        for i in range(5):
            self._set_label(self.lbl_edit_grid_r[i], "")

    def _draw_edit_level2(self):
        for i in range(5):
            self._set_label(self.lbl_edit_grid_r[i], "")
        press_label = "Short" if self.edit_press_idx == 0 else "Long"
        press_type  = "short" if self.edit_press_idx == 0 else "long"
        self._set_label(self.lbl_edit_title,
                        "%s > %s" % (self.BTN_ABBREV[self.edit_btn_idx], press_label))
        cfg = self.config[self.edit_btn_idx]
        params = self._get_edit_params(cfg, press_type)
        start = max(0, self.edit_menu_idx - 4)
        for row in range(5):
            mi = start + row
            if mi < len(params):
                item = params[mi]
                is_sel  = (mi == self.edit_menu_idx)
                is_edit = is_sel and self.edit_editing_value
                if item in ("Color", "Col.A", "Col.B", "Col.C", "Col.D"):
                    if is_edit:
                        # 편집 중: 활성 컴포넌트만 표시
                        comp = "RGB"[self.edit_color_comp]
                        val = self._get_param_value(cfg, press_type, item)
                        # val = "(R,G,B)" → 해당 컴포넌트 값 추출
                        nums = [int(x) for x in val.strip("()").split(",")]
                        text = "*%s.%s:%d" % (item, comp, nums[self.edit_color_comp])
                    else:
                        val = self._get_param_value(cfg, press_type, item)
                        text = "%s%s:%s" % (">" if is_sel else " ", item, val)
                else:
                    val = self._get_param_value(cfg, press_type, item)
                    prefix = "*" if is_edit else (">" if is_sel else " ")
                    text = "%s%-7s:%s" % (prefix, item, val)
                self._set_label(self.lbl_edit_lines[row], text)
                self.lbl_edit_lines[row].color = 0x00FF00 if is_sel else 0xAAAAAA
            else:
                self._set_label(self.lbl_edit_lines[row], "")

    def _get_param_value(self, cfg, press_type, item):
        if item == "Type":
            return cfg.get(press_type, {}).get("type", "none")
        elif item == "Target":
            a = cfg.get(press_type, {})
            t = a.get("type", "none")
            if t in ("effect", "channel_rotation"):
                return a.get("effect", "?")
            elif t == "scene":
                return str(a.get("number", "?"))
            elif t == "looper":
                btn = a.get("button", 0)
                return LOOPER_BTN_NAMES[btn] if btn < len(LOOPER_BTN_NAMES) else "?"
            return "-"
        elif item == "Color":
            c = cfg.get("color_" + press_type, [0, 0, 0])
            return "(%d,%d,%d)" % (c[0], c[1], c[2])
        elif item.startswith("Ch."):
            ch = "ABCD".index(item[-1])
            channels = cfg.get(press_type, {}).get("channels", [0, 1, 2, 3])
            return "ON" if ch in channels else "OFF"
        elif item.startswith("Col."):
            ch = "ABCD".index(item[-1])
            raw = cfg.get(press_type, {}).get("ch_colors", list(CHANNEL_COLORS))
            c = list(raw[ch]) if ch < len(raw) else [0, 0, 0]
            return "(%d,%d,%d)" % (c[0], c[1], c[2])
        elif item == "Back":
            return ""
        return ""

    # --------------------------------------------------------
    # Welcome screen
    # --------------------------------------------------------
    def show_welcome(self):
        splash = displayio.Group()
        bg = displayio.Bitmap(240, 240, 1)
        pal = displayio.Palette(1)
        pal[0] = 0x000000
        splash.append(displayio.TileGrid(bg, pixel_shader=pal))

        title = label.Label(
            terminalio.FONT, text="MIDI Captain",
            color=0xFFFFFF, scale=3,
            anchor_point=(0.5, 0.5), anchored_position=(120, 100),
        )
        splash.append(title)

        subtitle = label.Label(
            terminalio.FONT, text="for FM3",
            color=0x00AAFF, scale=2,
            anchor_point=(0.5, 0.5), anchored_position=(120, 140),
        )
        splash.append(subtitle)

        self.display.show(splash)
        self.display.refresh()

        colors = [
            (255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 255, 128), (0, 0, 255),
            (255, 0, 255), (255, 128, 0), (0, 204, 204), (0, 0, 255), (0, 128, 255),
        ]
        for i in range(NUM_BUTTONS):
            base = i * LEDS_PER_BUTTON
            color = colors[i % len(colors)]
            for j in range(LEDS_PER_BUTTON):
                self.leds.pixels[base + j] = color
            self.leds.pixels.show()
            time.sleep(0.15)

        time.sleep(0.5)

        for i in range(NUM_PIXELS):
            self.leds.pixels[i] = (0, 0, 0)
        self.leds.pixels.show()

    # --------------------------------------------------------
    # Polling — round-robin: 한 루프에 하나만 전송
    # --------------------------------------------------------
    def _do_polling(self, now):
        if now - self.poll_timer < self.poll_interval:
            return
        self.poll_timer = now
        self.poll_count += 1

        # 매 틱(0.15s): scene 조회 (응답 ~10B, 즉각적인 scene 변경 감지)
        # 3-슬롯 로테이션(0.45s 주기): status_dump / patch name / scene name+tempo
        # status_dump 응답(~수백B, 전송 ~100ms)을 매 틱 요청하면 UART 대역폭이
        # 포화되어 이름 응답이 밀리므로 0.45s로 낮춤
        self.send_get_scene()

        slot = self.poll_count % 3
        if slot == 0:
            self.send_status_dump()
        elif slot == 1:
            self.send_query_patch_name()
        else:
            self.send_query_scene_name()
            self.send_get_tempo()
            # 현재 페이지에 looper 버튼이 있을 때만 상태 조회
            if self.looper_buttons:
                self.send_get_looper()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------
    def run(self):
        print("FM3 Controller Started")
        self.show_welcome()
        # welcome 동안(~1.5s) 쌓인 미수신 데이터 폐기 — 깨진 SysEx 조각 방지
        self.uart.reset_input_buffer()
        # 초기 상태 일괄 조회 (폴링 로테이션 대기 없이)
        self.send_status_dump()
        self.send_get_scene()
        self.send_get_tempo()
        self.send_query_patch_name()
        self.send_query_scene_name()
        gc_count = 0

        while True:
            now = time.monotonic()

            # 1. Rotary encoder (반응성 우선 — 가장 먼저 처리)
            self.process_encoder()

            # 2. Buttons
            self.process_buttons()

            # 3. MIDI IN
            self.process_midi_in()

            # 4. Polling / Tap LED — edit mode 중에는 생략
            #    (status_dump 파싱이 루프를 수십 ms 지연시켜 로터리 반응성 저하)
            if not self.edit_mode:
                # preset 변경 직후 예약된 이름 조회 (폴링 로테이션 대기 없이)
                if self.name_query_at and now >= self.name_query_at:
                    self.name_query_at = 0
                    self.send_query_patch_name()
                self._do_polling(now)
                for i in self.tap_buttons:
                    self._update_tap_led(i)

            # 6. Tuner timeout
            if self.tuner_active and now - self.tuner_last_data > 5.0:
                self.tuner_active = False
                self.display_dirty = True

            # 7. LED write (pixels.show() 1회)
            self.leds.update()

            # 8. Display (label.text만 변경, group 전환시만 display.show())
            if self.edit_mode:
                self._update_edit_display()
            else:
                self._update_display()

            # 10. GC
            gc_count += 1
            if gc_count >= 1000:
                gc.collect()
                gc_count = 0

# ============================================================
if __name__ == "__main__":
    FM3Controller().run()
