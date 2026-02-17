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
TAP_TEMPO_FUNC = 0x10
TUNER_FUNC = 0x11
STATUS_DUMP = 0x13
SET_GET_TEMPO = 0x14

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
    "FUZZ1": 118, "FUZZ2": 119,
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
DEFAULT_CONFIG = [
    # switch1: scene 1 short / scene 5 long
    {
        "short": {"type": "scene", "number": 1},
        "long": {"type": "scene", "number": 5},
        "color_short": [255, 0, 0],
        "color_long": [0, 255, 0],
    },
    # switch2: scene 2 / scene 6
    {
        "short": {"type": "scene", "number": 2},
        "long": {"type": "scene", "number": 6},
        "color_short": [255, 0, 0],
        "color_long": [0, 255, 0],
    },
    # switch3: scene 3 / scene 7
    {
        "short": {"type": "scene", "number": 3},
        "long": {"type": "scene", "number": 7},
        "color_short": [255, 0, 0],
        "color_long": [0, 255, 0],
    },
    # switch4: scene 4 / scene 8
    {
        "short": {"type": "scene", "number": 4},
        "long": {"type": "scene", "number": 8},
        "color_short": [255, 0, 0],
        "color_long": [0, 255, 0],
    },
    # switchUp: preset_inc / preset_dec
    {
        "short": {"type": "preset_inc"},
        "long": {"type": "preset_dec"},
        "color_short": [0, 0, 255],
        "color_long": [0, 0, 255],
    },
    # switchA: comp1 toggle
    {
        "short": {"type": "effect", "effect": "COMP1"},
        "long": {"type": "none"},
        "color_short": [255, 255, 0],
        "color_long": [0, 0, 0],
    },
    # switchB: drive1 toggle / channel rotation
    {
        "short": {"type": "effect", "effect": "FUZZ1"},
        "long": {"type": "channel_rotation", "effect": "FUZZ1"},
        "color_short": [255, 128, 0],
        "color_long": [0, 0, 0],
    },
    # switchC: chorus1 toggle
    {
        "short": {"type": "effect", "effect": "CHORUS1"},
        "long": {"type": "none"},
        "color_short": [0, 204, 204],
        "color_long": [0, 0, 0],
    },
    # switchD: delay1 toggle
    {
        "short": {"type": "effect", "effect": "DELAY1"},
        "long": {"type": "none"},
        "color_short": [0, 0, 255],
        "color_long": [0, 0, 0],
    },
    # switchDown: tap tempo / tuner
    {
        "short": {"type": "tap_tempo"},
        "long": {"type": "tuner"},
        "color_short": [0, 0, 255],
        "color_long": [0, 0, 255],
    },
]

CONFIG_FILE = "config.json"


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        if isinstance(cfg, list) and len(cfg) == NUM_BUTTONS:
            return cfg
    except (OSError, ValueError):
        pass
    return [dict(d) for d in DEFAULT_CONFIG]


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

    def set_button_color(self, btn_idx, color):
        if 0 <= btn_idx < self.num_buttons:
            c = tuple(color)
            self.button_leds[btn_idx] = [c, c, c]

    def set_button_leds(self, btn_idx, led1, led2, led3):
        if 0 <= btn_idx < self.num_buttons:
            self.button_leds[btn_idx] = [tuple(led1), tuple(led2), tuple(led3)]

    def update(self):
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
        self.config = load_config()
        self.long_press_time = 0.5  # seconds

        # MIDI setup
        self.uart = busio.UART(
            tx=board.GP16, rx=board.GP17,
            baudrate=31250, timeout=0.02
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
        self.scene_name = ""
        self.tempo_bpm = 120

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
        self.edit_level = 0       # 0=button select, 1=param edit
        self.edit_btn_idx = 0
        self.edit_menu_idx = 0
        self.edit_editing_value = False

        # Build lookup tables
        self._build_lookups()

        # Pre-build display groups (한 번만 생성, text만 업데이트)
        self._init_display_groups()

    def _build_lookups(self):
        self.fx_to_btn = {}      # fx_id → list of button indices
        self.scene_buttons = []  # button indices with scene type
        self.tap_buttons = []    # button indices with tap_tempo type
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
        for row in range(4):
            l = label.Label(
                terminalio.FONT, text="",
                color=0xAAAAAA, scale=2,
                anchor_point=(0.0, 0.0), anchored_position=(5, 50 + row * 45),
            )
            self.grp_edit.append(l)
            self.lbl_edit_lines.append(l)

        # Track which group is currently shown
        self._current_group = None

    def _show_group(self, grp):
        if self._current_group is not grp:
            self.display.show(grp)
            self.display.refresh()
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

    def send_patch_inc(self):
        self.midi.send(ControlChange(41, 127))

    def send_patch_dec(self):
        self.midi.send(ControlChange(42, 127))

    # --------------------------------------------------------
    # MIDI receive processing
    # --------------------------------------------------------
    def process_midi_in(self):
        msg = self.midi.receive()
        if not isinstance(msg, SystemExclusive):
            return

        data = msg.data
        if len(data) < 2 or data[0] != FM3_MODEL_ID:
            return

        func = data[1]

        if func == TAP_TEMPO_FUNC:
            self.tap.on_tap()
            self.display_dirty = True
            return

        if func == STATUS_DUMP:
            self._parse_status_dump(data)
            return

        if func == SET_SCENE:
            if len(data) >= 3:
                self.current_scene = data[2]
                self._update_scene_leds()
                self.display_dirty = True
            return

        if func == QUERY_PATCH_NAME:
            if len(data) >= 36:
                name_bytes = data[4:36]
                self.patch_name = "".join(chr(b) for b in name_bytes if 32 <= b < 127).strip()
                self.display_dirty = True
            return

        if func == QUERY_SCENE_NAME:
            if len(data) >= 35:
                name_bytes = data[3:35]
                self.scene_name = "".join(chr(b) for b in name_bytes if 32 <= b < 127).strip()
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
                lo = data[2]
                hi = data[3]
                self.tempo_bpm = lo + hi * 128
                self.tap.interval = 60.0 / self.tempo_bpm if self.tempo_bpm > 0 else 0.5
                self.display_dirty = True
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
        packets = data[2:-1]
        for i in range(0, len(packets) - 2, 3):
            id_lo = packets[i]
            id_hi = packets[i + 1]
            dd = packets[i + 2]
            fx_id = int(id_lo) + 128 * int(id_hi)
            bypassed = bool(dd & 0x01)
            channel = (dd >> 1) & 0x07
            num_ch = (dd >> 4) & 0x07

            # 변경된 effect만 LED 업데이트
            old_bypassed = self.fx_states.get(fx_id)
            old_channel = self.fx_channels.get(fx_id)
            self.fx_states[fx_id] = bypassed
            self.fx_channels[fx_id] = channel
            self.fx_num_channels[fx_id] = num_ch

            if (old_bypassed != bypassed or old_channel != channel) and fx_id in self.fx_to_btn:
                for btn_idx in self.fx_to_btn[fx_id]:
                    self._update_button_leds(btn_idx)

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

        elif atype == "channel_toggle":
            fx_name = action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is None:
                return
            cur_ch = self.fx_channels.get(fx_id, 0)
            new_ch = 1 if cur_ch == 0 else 0
            self.send_set_channel(fx_id, new_ch)
            self.fx_channels[fx_id] = new_ch
            self._update_channel_leds(fx_id)
            self._show_temp(fx_name, "CH.%s" % chr(65 + new_ch))

        elif atype == "channel_rotation":
            fx_name = action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is None:
                return
            cur_ch = self.fx_channels.get(fx_id, 0)
            num_ch = self.fx_num_channels.get(fx_id, 4)
            if num_ch < 2:
                num_ch = 4
            new_ch = (cur_ch + 1) % num_ch
            self.send_set_channel(fx_id, new_ch)
            self.fx_channels[fx_id] = new_ch
            self._update_channel_leds(fx_id)
            self._show_temp(fx_name, "CH.%s" % chr(65 + new_ch))

        elif atype == "tuner":
            self.tuner_active = not self.tuner_active
            self.send_tuner(self.tuner_active)
            self._show_temp("TUNER", "ON" if self.tuner_active else "OFF")

        elif atype == "preset_inc":
            self.send_patch_inc()
            self._show_temp("PRESET", "+")
            self._blink_button(idx)

        elif atype == "preset_dec":
            self.send_patch_dec()
            self._show_temp("PRESET", "-")
            self._blink_button(idx)

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

        # Effect toggle + channel toggle/rotation combo
        if s_type == "effect" and l_type in ("channel_toggle", "channel_rotation"):
            fx_name = short_action.get("effect", "")
            fx_id = EFFECT_IDS.get(fx_name)
            if fx_id is not None:
                ch = self.fx_channels.get(fx_id, 0)
                ch_color = CHANNEL_COLORS[ch % len(CHANNEL_COLORS)]
                bypassed = self.fx_states.get(fx_id, True)
                color = ch_color if not bypassed else color_off(ch_color)
                self.leds.set_button_color(idx, color)
                return

        # Two Type A (short=A1, long=A2)
        if s_type in ("effect", "scene") and l_type in ("effect", "scene"):
            c1 = self._get_action_color(idx, "short")
            c2 = self._get_action_color(idx, "long")
            avg = color_avg(c1, c2)
            self.leds.set_button_leds(idx, c1, c2, avg)
            return

        # Single action
        if s_type in ("effect", "scene") and l_type == "none":
            color = self._get_action_color(idx, "short")
            self.leds.set_button_color(idx, color)
            return

        # Type A + Type C
        if s_type in ("effect", "scene") and l_type in ("tuner", "preset_inc", "preset_dec"):
            color = self._get_action_color(idx, "short")
            self.leds.set_button_color(idx, color)
            return

        # Type C + Type A
        if s_type in ("tuner", "preset_inc", "preset_dec") and l_type in ("effect", "scene"):
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
                bypassed = self.fx_states.get(fx_id, True)
                return color if not bypassed else color_off(color)
            return color_off(color)

        if atype == "scene":
            scene_num = action.get("number", 1) - 1
            if self.current_scene == scene_num:
                return color
            return color_off(color)

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

    def _refresh_display(self, now):
        """수동 display refresh — throttled"""
        if now - self.display_refresh_timer >= self.display_refresh_interval:
            self.display_refresh_timer = now
            self.display.refresh()

    # --------------------------------------------------------
    # Edit mode (Rotary Encoder)
    # --------------------------------------------------------
    EDIT_TYPES = ["effect", "scene", "tap_tempo", "tuner", "preset_inc", "preset_dec",
                  "channel_toggle", "channel_rotation", "none"]
    EFFECT_LIST = list(EFFECT_IDS.keys())

    def process_encoder(self):
        sw_state = not self.encoder_sw.value
        now = time.monotonic()

        if sw_state and not self.encoder_sw_pressed:
            self.encoder_sw_pressed = True
            self.encoder_sw_press_time = now
        elif not sw_state and self.encoder_sw_pressed:
            self.encoder_sw_pressed = False
            duration = now - self.encoder_sw_press_time
            if duration >= 2.0:
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

    def _enter_edit_mode(self):
        self.edit_mode = True
        self.edit_level = 0
        self.edit_btn_idx = 0
        self.edit_menu_idx = 0
        self.edit_editing_value = False
        self.display_dirty = True

    def _exit_edit_mode(self):
        self.edit_mode = False
        save_config(self.config)
        self._build_lookups()
        self._update_all_button_leds()
        self.display_dirty = True

    def _edit_click(self):
        if self.edit_level == 0:
            self.edit_level = 1
            self.edit_menu_idx = 0
            self.edit_editing_value = False
        elif self.edit_level == 1:
            menu_items = self._get_edit_menu()
            if self.edit_menu_idx >= len(menu_items):
                return
            item = menu_items[self.edit_menu_idx]
            if item == "Back":
                self.edit_level = 0
                self.edit_editing_value = False
            else:
                self.edit_editing_value = not self.edit_editing_value
        self.display_dirty = True

    def _edit_rotate(self, delta):
        if self.edit_level == 0:
            self.edit_btn_idx = (self.edit_btn_idx + delta) % NUM_BUTTONS
        elif self.edit_level == 1:
            if self.edit_editing_value:
                self._edit_change_value(delta)
            else:
                menu_items = self._get_edit_menu()
                self.edit_menu_idx = (self.edit_menu_idx + delta) % len(menu_items)
        self.display_dirty = True

    def _get_edit_menu(self):
        return ["Short Type", "Short Target", "Long Type", "Long Target",
                "Color S R", "Color S G", "Color S B",
                "Color L R", "Color L G", "Color L B",
                "Back"]

    def _edit_change_value(self, delta):
        cfg = self.config[self.edit_btn_idx]
        menu_items = self._get_edit_menu()
        item = menu_items[self.edit_menu_idx]

        if item == "Short Type":
            cur = cfg.get("short", {}).get("type", "none")
            i = self.EDIT_TYPES.index(cur) if cur in self.EDIT_TYPES else 0
            i = (i + delta) % len(self.EDIT_TYPES)
            cfg.setdefault("short", {})["type"] = self.EDIT_TYPES[i]
        elif item == "Short Target":
            self._edit_target(cfg, "short", delta)
        elif item == "Long Type":
            cur = cfg.get("long", {}).get("type", "none")
            i = self.EDIT_TYPES.index(cur) if cur in self.EDIT_TYPES else 0
            i = (i + delta) % len(self.EDIT_TYPES)
            cfg.setdefault("long", {})["type"] = self.EDIT_TYPES[i]
        elif item == "Long Target":
            self._edit_target(cfg, "long", delta)
        elif item.startswith("Color S"):
            ch = item[-1]
            c = list(cfg.get("color_short", [0, 0, 0]))
            ci = "RGB".index(ch)
            c[ci] = max(0, min(255, c[ci] + delta * 5))
            cfg["color_short"] = c
        elif item.startswith("Color L"):
            ch = item[-1]
            c = list(cfg.get("color_long", [0, 0, 0]))
            ci = "RGB".index(ch)
            c[ci] = max(0, min(255, c[ci] + delta * 5))
            cfg["color_long"] = c

    def _edit_target(self, cfg, press_type, delta):
        action = cfg.get(press_type, {})
        atype = action.get("type", "none")
        if atype == "effect":
            cur = action.get("effect", "")
            i = self.EFFECT_LIST.index(cur) if cur in self.EFFECT_LIST else 0
            i = (i + delta) % len(self.EFFECT_LIST)
            action["effect"] = self.EFFECT_LIST[i]
        elif atype == "scene":
            cur = action.get("number", 1)
            action["number"] = max(1, min(8, cur + delta))
        elif atype in ("channel_toggle", "channel_rotation"):
            cur = action.get("effect", "")
            i = self.EFFECT_LIST.index(cur) if cur in self.EFFECT_LIST else 0
            i = (i + delta) % len(self.EFFECT_LIST)
            action["effect"] = self.EFFECT_LIST[i]

    def _update_edit_display(self):
        if not self.display_dirty:
            return
        self.display_dirty = False

        if self.edit_level == 0:
            sw_name = BUTTON_NAMES[self.edit_btn_idx]
            self._set_label(self.lbl_edit_title, "[EDIT] Select SW")
            self._set_label(self.lbl_edit_lines[0], "")
            self._set_label(self.lbl_edit_lines[1], "> %s" % sw_name)
            self.lbl_edit_lines[1].color = 0xFFFFFF
            self._set_label(self.lbl_edit_lines[2], "")
            self._set_label(self.lbl_edit_lines[3], "")
        else:
            cfg = self.config[self.edit_btn_idx]
            sw_name = BUTTON_NAMES[self.edit_btn_idx]
            self._set_label(self.lbl_edit_title, "[EDIT] %s" % sw_name)

            menu_items = self._get_edit_menu()
            start = max(0, self.edit_menu_idx - 1)
            end = min(len(menu_items), start + 4)
            for row in range(4):
                mi = start + row
                if mi < end:
                    item = menu_items[mi]
                    val = self._get_edit_value_str(cfg, item)
                    prefix = ">" if mi == self.edit_menu_idx else " "
                    if mi == self.edit_menu_idx and self.edit_editing_value:
                        prefix = "*"
                    self._set_label(self.lbl_edit_lines[row], "%s %s: %s" % (prefix, item[:8], val[:10]))
                    self.lbl_edit_lines[row].color = 0x00FF00 if mi == self.edit_menu_idx else 0xAAAAAA
                else:
                    self._set_label(self.lbl_edit_lines[row], "")

        self._show_group(self.grp_edit)

    def _get_edit_value_str(self, cfg, item):
        if item == "Short Type":
            return cfg.get("short", {}).get("type", "none")
        elif item == "Short Target":
            a = cfg.get("short", {})
            t = a.get("type", "none")
            if t in ("effect", "channel_toggle", "channel_rotation"):
                return a.get("effect", "?")
            elif t == "scene":
                return str(a.get("number", "?"))
            return "-"
        elif item == "Long Type":
            return cfg.get("long", {}).get("type", "none")
        elif item == "Long Target":
            a = cfg.get("long", {})
            t = a.get("type", "none")
            if t in ("effect", "channel_toggle", "channel_rotation"):
                return a.get("effect", "?")
            elif t == "scene":
                return str(a.get("number", "?"))
            return "-"
        elif item == "Color S R":
            return str(cfg.get("color_short", [0, 0, 0])[0])
        elif item == "Color S G":
            return str(cfg.get("color_short", [0, 0, 0])[1])
        elif item == "Color S B":
            return str(cfg.get("color_short", [0, 0, 0])[2])
        elif item == "Color L R":
            return str(cfg.get("color_long", [0, 0, 0])[0])
        elif item == "Color L G":
            return str(cfg.get("color_long", [0, 0, 0])[1])
        elif item == "Color L B":
            return str(cfg.get("color_long", [0, 0, 0])[2])
        elif item == "Back":
            return "<-"
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

        # 매번 status_dump, 3번째마다 get_scene/get_tempo, 13번째마다 patch/scene name
        self.send_status_dump()

        if self.poll_count % 3 == 0:
            self.send_get_scene()

        if self.poll_count % 3 == 1:
            self.send_get_tempo()

        if self.poll_count % 13 == 0:
            self.send_query_patch_name()

        if self.poll_count % 13 == 7:
            self.send_query_scene_name()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------
    def run(self):
        print("FM3 Controller Started")
        self.show_welcome()
        self.send_status_dump()
        self.send_get_scene()
        gc_count = 0

        while True:
            now = time.monotonic()

            # 1. Buttons
            self.process_buttons()

            # 2. MIDI IN
            self.process_midi_in()

            # 3. Polling (round-robin)
            self._do_polling(now)

            # 4. Rotary encoder
            self.process_encoder()

            # 5. Tap tempo LED update (경량: 직접 색상 설정만)
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

            # 9. Display refresh (수동 — auto_refresh=False)
            self._refresh_display(now)

            # 10. GC
            gc_count += 1
            if gc_count >= 1000:
                gc.collect()
                gc_count = 0

# ============================================================
if __name__ == "__main__":
    FM3Controller().run()
