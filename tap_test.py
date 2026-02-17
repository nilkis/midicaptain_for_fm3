import time
import board
import keypad
import neopixel
import busio
import adafruit_midi
from adafruit_midi.system_exclusive import SystemExclusive
import gc

# ============================================================
# FM3 SysEx Protocol
# ============================================================
FRACTAL_MFR_ID = (0x00, 0x01, 0x74)
FM3_MODEL_ID = 0x11
GET_FX_STATUS = 0x13
SET_FX_STATUS = 0x0A
TAP_TEMPO_FUNC = 0x10
TUNER_FUNC = 0x11

# Effect IDs
EFFECT_IDS = {
    "COMPRESSOR1": 46,
    "DRIVE1": 118,
    "CHORUS1": 78,
    "DELAY1": 70,
}

# ============================================================
# Hardware Configuration
# ============================================================
BUTTON_PINS = (
#    board.GP0, board.GP2, board.GP3,
    board.GP1, board.GP25, board.GP24, board.GP23, board.GP20,
    board.GP9, board.GP10, board.GP11, board.GP18, board.GP19,
)
BUTTON_NAMES = (
#    'encoderSW', 'encoderA', 'encoderB',
    'switch1', 'switch2', 'switch3', 'switch4', 'switchUp',
    'switchA', 'switchB', 'switchC', 'switchD', 'switchDown',
)

COLOR_ENGAGED = (
    (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 255),
    (100, 100, 0), (255, 0, 0), (0, 204, 204), (0, 0, 255), (0, 0, 0),
)
OFF_W = 0.02
COLOR_BYPASSED = [[int(x[0]*OFF_W), int(x[1]*OFF_W) , int(x[2]*OFF_W)] for x in COLOR_ENGAGED]
COLOR_TAP_FLASH = (255, 80, 0)
COLOR_TAP_IDLE = (0, 80, 255)  # Tap 버튼 기본색 (파랑)

# 버튼 설정: effect 또는 tap_tempo
BUTTON_CONFIG = [
    {"type": "none"},
    {"type": "none"},
    {"type": "none"},
    {"type": "none"},
    {"type": "tuner", "color": (0, 0, 255)},
    {"type": "effect", "effect": "COMPRESSOR1"},
    {"type": "effect", "effect": "DRIVE1"},
    {"type": "effect", "effect": "CHORUS1"},
    {"type": "effect", "effect": "DELAY1"},
    {"type": "tap_tempo"},
]

NEOPIXEL_PIN = board.GP7
NUM_PIXELS = 30    # BUTTON당 3개
TAP_TEMPO_IDX = 9  # Tap Tempo 버튼의 LED

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
            if 0.2 < gap < 2.0:  # 30-300 BPM
                self.interval = gap
        self.last_tap = now
        self._beat_start = now  # 비트 시작점 리셋

    def is_flashing(self):
        """현재 tempo에 맞춰 주기적으로 flash 여부 반환"""
        if self.interval <= 0:
            return False

        now = time.monotonic()
        # 마지막 tap 이후 경과 시간
        elapsed = now - self._beat_start
        # 현재 beat 내 위치 (0.0 ~ 1.0)
        beat_phase = (elapsed % self.interval) / self.interval
        # beat 시작 부분에서만 flash (flash_duration 비율만큼)
        flash_ratio = self.flash_duration / self.interval
        return beat_phase < flash_ratio

    def bpm(self):
        return 60.0 / self.interval if self.interval > 0 else 120

# ============================================================
# LED Manager
# ============================================================
class LEDManager:
    def __init__(self, pin, count, tap_led_idx):
        self.pixels = neopixel.NeoPixel(pin, count, brightness=0.5, auto_write=False)
        self.count = count
        self.tap_led_idx = tap_led_idx
        self.engaged = [False] * (count // 3 + 1)

    def set_state(self, idx, is_engaged):
        if 0 <= idx < len(self.engaged):
            self.engaged[idx] = is_engaged

    def toggle(self, idx):
        if 0 <= idx < len(self.engaged):
            self.engaged[idx] = not self.engaged[idx]

    def update(self, tap_flash=False):
        # button당 3개의 LED가 있다.
        for i in range(self.count):
            bid = i // 3
            if bid == self.tap_led_idx:
                # Tap Tempo LED - tempo에 맞춰 깜빡임
                if tap_flash:
                    self.pixels[i] = COLOR_TAP_FLASH
                else:
                    self.pixels[i] = COLOR_TAP_IDLE
            else:
                # Effect LED
                if self.engaged[bid]:
                    self.pixels[i] = COLOR_ENGAGED[bid]
                else:
                    self.pixels[i] = COLOR_BYPASSED[bid]
        self.pixels.show()

    def _blend(self, c1, c2, ratio):
        return tuple(int(a * (1 - ratio) + b * ratio) for a, b in zip(c1, c2))

# ============================================================
# FM3 Controller
# ============================================================
class FM3Controller:
    def __init__(self,
                 short_press_time_ms=50,
                 long_press_time_ms=500,
                 ):
        self.short_press_time_ms = short_press_time_ms
        self.long_press_time_ms = long_press_time_ms

        # MIDI setup
        # timeout:
        # - 짧으면(0.001): timeout동안 완전한 메시지를 받지 못하면 유실됨
        #   TAP_TEMPO_FUNC는 수신되나 GET_FX_STATUS는 유실됨
        # - 길면(0.2): Button LED 응답속도가 느려짐
        self.uart = busio.UART(
            tx=board.GP16, rx=board.GP17,
            baudrate=31250, timeout=0.02
        )
        # in_buf_size: 최대 메시지 길이를 고려한다.(GET_FX_STATUS가 제일 길다)
        self.midi = adafruit_midi.MIDI(
            midi_in=self.uart, midi_out=self.uart,
            out_channel=0, debug=False, in_buf_size=64
        )

        self.keys = keypad.Keys(
            pins=BUTTON_PINS,
            value_when_pressed=False,
            pull=True,
            interval=0.02,
        )

        self.leds = LEDManager(NEOPIXEL_PIN, NUM_PIXELS, TAP_TEMPO_IDX)
        self.tap = TapTempo()

        self.fx_states = {}
        self.last_query = 0
        self.query_interval = 0.15

        # Effect ID → Button index
        self.fx_to_btn = {}
        for i, cfg in enumerate(BUTTON_CONFIG):
            if cfg["type"] == "effect":
                name = cfg["effect"]
                if name in EFFECT_IDS:
                    self.fx_to_btn[EFFECT_IDS[name]] = i

    def _calc_checksum(self, mid, data):
        checksum = 0xF0
        for byte in mid:
            checksum ^= byte
        for byte in data:
            checksum ^= byte
        return checksum & 0x7F

    def send_query(self):
        """Block Status 요청"""
        data = [FM3_MODEL_ID, GET_FX_STATUS]
        checksum = self._calc_checksum(FRACTAL_MFR_ID, data)
        data.append(checksum)
        self.midi.send(SystemExclusive(FRACTAL_MFR_ID, data))

    def send_tap_tempo(self):
        """Tap Tempo 전송: F0 00 01 74 11 10 cs F7"""
        data = [FM3_MODEL_ID, TAP_TEMPO_FUNC]
        cs = self._calc_checksum(FRACTAL_MFR_ID, data)
        data.append(cs)
        self.midi.send(SystemExclusive(FRACTAL_MFR_ID, data))

        # 로컬에서도 tap 처리 (즉시 LED 반응)
        self.tap.on_tap()

    def parse_fx_status(self, data):
        if len(data) < 4 or data[1] != GET_FX_STATUS:
            return False

        packets = data[2:-1]
        for i in range(0, len(packets) - 2, 3):
            id_lo = packets[i]
            id_hi = packets[i + 1]
            dd = packets[i + 2]

            fx_id = int(id_lo) + 128 * int(id_hi)
            bypassed = bool(dd & 0x01)
            channel = (dd >> 1) & 0x07

            self.fx_states[fx_id] = bypassed

        for fx_id, btn_idx in self.fx_to_btn.items():
            if fx_id in self.fx_states:
                self.leds.set_state(btn_idx, not self.fx_states[fx_id])

        return True

    def handle_button(self, idx):
        cfg = BUTTON_CONFIG[idx]

        btype = cfg["type"]

        data = None
        if btype == "tuner":
            data = [FM3_MODEL_ID, TUNER_FUNC, 1]
        elif btype == "effect":
            # Effect toggle
            fx_id = EFFECT_IDS.get(cfg["effect"], 0)
            if fx_id and fx_id in self.fx_states:
                value = 0 if self.fx_states[fx_id] else 1
            else:
                value = 0

            b2 = int(fx_id) // 128
            b1 = int(fx_id) % 128
            data = [FM3_MODEL_ID, SET_FX_STATUS, b1, b2, value]

            # ★ 즉시 LED 토글 (낙관적 업데이트)
            self.leds.toggle(idx)
            # 내부 상태도 업데이트
            if fx_id:
                self.fx_states[fx_id] = not self.fx_states.get(fx_id, True)

        if data:
            data.append(self._calc_checksum(FRACTAL_MFR_ID, data))
            msg = SystemExclusive(manufacturer_id=FRACTAL_MFR_ID, data=data)
            self.midi.send(msg)

    def process_buttons(self):
        """★ keypad 이벤트 큐에 쌓인 버튼 이벤트 처리"""
        while event := self.keys.events.get():
            if event.pressed and event.key_number == TAP_TEMPO_IDX:
                self.send_tap_tempo()
            if event.released:
                self.handle_button(event.key_number)

    def process_midi_in(self):
        msg = self.midi.receive()
        if not isinstance(msg, SystemExclusive):
            return
        #if msg.manufacturer_id != FRACTAL_MFR_ID:
        #    return

        data = msg.data

        if len(data) < 3 or data[0] != FM3_MODEL_ID:
            return

        # Tap Tempo from FM3
        if data[1] == TAP_TEMPO_FUNC:
            self.tap.on_tap()
            return

        # Block Status
        self.parse_fx_status(data)

    def run(self):
        print("FM3 Controller Started")
        self.send_query()
        gc_count = 0

        while True:
            now = time.monotonic()

            # 1. Buttons
            self.process_buttons()

            # 2. MIDI IN
            self.process_midi_in()

            # 3. Periodic query
            if now - self.last_query > self.query_interval:
                self.send_query()
                self.last_query = now

            # 4. LED update
            self.leds.update(tap_flash=self.tap.is_flashing())

            # 5. GC
            gc_count += 1
            if gc_count >= 1000:
                gc.collect()
                gc_count = 0

# ============================================================
if __name__ == "__main__":
    FM3Controller().run()
