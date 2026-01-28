import time
import board
import digitalio
import keypad
import busio
import adafruit_midi
from adafruit_midi.system_exclusive import SystemExclusive
from adafruit_midi.control_change import ControlChange
import neopixel
import gc

# ============================================================
# FM3 SysEx Protocol Constants
# ============================================================
FRACTAL_MFR_ID = (0x00, 0x01, 0x74)
FM3_MODEL_ID = 0x11
GET_FX_STATUS = 0x13

# ============================================================
# Button Configuration (10 buttons)
# ============================================================
BUTTON_NAMES = (
#    'encoderSW', 'encoderA', 'encoderB',
    'switch1', 'switch2', 'switch3', 'switch4', 'switchUp',
    'switchA', 'switchB', 'switchC', 'switchD', 'switchDown',
)
BUTTON_PINS = (
#    board.GP0, board.GP2, board.GP3,
    board.GP1, board.GP25, board.GP24, board.GP23, board.GP20,
    board.GP9, board.GP10, board.GP11, board.GP18, board.GP19,
)
BUTTON_COLORS_ON = (
#    (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (100, 100, 0), (255, 0, 0), (0, 204, 204), (0, 0, 255), (0, 0, 0),    # cyan,red,yellow,blue , ,
)
OFF_W = 0.02
BUTTON_COLORS_OFF = [[int(x[0]*OFF_W), int(x[1]*OFF_W) , int(x[2]*OFF_W)] for x in BUTTON_COLORS_ON]

EFFECT_NAMES = (
#    "NONE", "NONE", "NONE",
    "NONE", "NONE", "NONE", "NONE", "NONE",
    "COMPRESSOR1", "DRIVE1", "CHORUS1", "DELAY1", "NONE",
)
EFFECT_IDS = (
#    999, 999, 999,
    999, 999, 999, 999, 999,
    46, 118, 78, 70, 999,
)
EFFECT_MAPS = {EFFECT_IDS[i]:EFFECT_NAMES[i] for i in range(len(EFFECT_NAMES))}

# ============================================================
# Checksum
# ============================================================
def calc_checksum(mid, data):
    checksum = 0
    checksum ^= 0xF0
    for byte in mid:
        checksum ^= byte
    for byte in data:
        checksum ^= byte
    return checksum & 0x7F # Keep it a 7-bit value

class FM3Controller:
    def __init__(self,
                 short_press_time_ms=50,
                 long_press_time_ms=500,
                 ):
        self.short_press_time_ms = short_press_time_ms
        self.long_press_time_ms = long_press_time_ms

        self.keys = keypad.Keys(
            pins=BUTTON_PINS,
            value_when_pressed=False,
            pull=True,
            interval=0.02,  # 20ms debounce
        )
        self.event_time = [0] * len(BUTTON_PINS)

        self.last_query_time = 0
        self.query_interval = 0.15

        # Effect ID to button index mapping
        self.effect_to_button = {}
        self.effect_states = {}
        for i, name in enumerate(EFFECT_NAMES):
            if name != "NONE":
                effect_id = EFFECT_IDS[i]
                self.effect_to_button[effect_id] = i
                self.effect_states[effect_id] = {
                    "bypassed": 1,
                    "channel": 0,
                }
        # MIDI setup
        self.uart = busio.UART(tx=board.GP16, rx=board.GP17,
                               baudrate=31250, timeout=0.2001)  # Adjust baudrate if needed
        # Create a MIDI object using the UART
        self.midi = adafruit_midi.MIDI(midi_in=self.uart, midi_out=self.uart,
                                       out_channel=0, debug=False, in_buf_size=64)

        # Button Colors

        self.pixels = neopixel.NeoPixel(board.GP7, 30, auto_write=False, brightness=1)

    def update_leds(self):
        """Effect 상태에 따라 LED 업데이트"""
        for effect_id, button_idx in self.effect_to_button.items():
            if effect_id in self.effect_states:
                engaged = not self.effect_states[effect_id]["bypassed"]
                if engaged:
                    col = BUTTON_COLORS_ON[button_idx]
                else:
                    col = BUTTON_COLORS_OFF[button_idx]

                pidx = button_idx * 3
                self.pixels[pidx] = self.pixels[pidx + 1] = self.pixels[pidx + 2] = col
                self.pixels.show()

    def _change_effect_bypass(self, effect_id, value):
        mid = [0x00, 0x01,0x74]
        b2 = int(effect_id)//128
        b1 = int(effect_id)%128
        data = [FM3_MODEL_ID, 0x0A, b1, b2, value]
        data.append(calc_checksum(mid, data))
        msg = SystemExclusive(manufacturer_id=mid, data=data)
        self.midi.send(msg)

    def _handle_button_press(self, button_idx):
        """버튼 눌림 처리"""
        name = BUTTON_NAMES[button_idx]
        if self.event_time[button_idx] >= self.long_press_time_ms:
            '''long press'''
            print(f">>>> {name} long pressed. event_time={self.event_time[button_idx]}")
        else:
            '''short press'''
            print(f">>>> {name} short pressed. event_time={self.event_time[button_idx]}")

        effect_id = EFFECT_IDS[button_idx]
        value = not self.effect_states[effect_id]['bypassed']
        self._change_effect_bypass(effect_id, value)

    def process_buttons(self):
        """★ keypad 이벤트 큐에서 버튼 이벤트 처리"""
        # 큐에 쌓인 모든 이벤트 처리
        while True:
            event = self.keys.events.get()
            if event is None:
                break
            if event.pressed:
                self.event_time[event.key_number] = event.timestamp
            if event.released:
                self.event_time[event.key_number] = event.timestamp - self.event_time[event.key_number]
                self._handle_button_press(event.key_number)

    def parse_status_response(self, data):
        """FM3 응답 파싱"""
        if len(data) < 3:
            return False

        if data[0] != FM3_MODEL_ID or data[1] != GET_FX_STATUS:
            return False

        packets_data = data[2:-1]

        for i in range(0, len(packets_data) - 2, 3):
            id_lo = packets_data[i]
            id_hi = packets_data[i + 1]
            dd = packets_data[i + 2]

            #effect_id = id_lo | (id_hi << 7)
            effect_id = int(id_lo) + 128 * int(id_hi)
            bypassed = bool(dd & 0x01)
            channel = (dd >> 1) & 0x07

            if effect_id in self.effect_states:
                prev_bypass = self.effect_states[effect_id]["bypassed"]
                if prev_bypass != bypassed:
                    print(f"effect: id={effect_id} changed. {prev_bypass} -> {bypassed}")
            self.effect_states[effect_id] = {
                "bypassed": bypassed,
                "channel": channel,
            }

        return True

    def process_midi_in(self):
        """MIDI IN 처리"""
        msg = self.midi.receive()
        if msg is None:
            return

        if isinstance(msg, SystemExclusive):
            if self.parse_status_response(bytes(msg.data)):
                self.update_leds()

    def send_status_query(self):
        """FM3에 모든 Effect 상태 요청"""
        data = [FM3_MODEL_ID, GET_FX_STATUS]
        checksum = calc_checksum(FRACTAL_MFR_ID, data)
        data.append(checksum)
        sysex = SystemExclusive(
            FRACTAL_MFR_ID,
            data
        )
        self.midi.send(sysex)

    def run(self):
        """메인 루프"""
        print("FM3 Controller Started (keypad version)")
        gc_counter = 0

        while True:
            now = time.monotonic()

            # 1. ★ 버튼 이벤트 처리 (큐에서 가져옴 - 놓치지 않음)
            self.process_buttons()

            # 2. MIDI IN 처리
            self.process_midi_in()

            # 3. 주기적 상태 쿼리
            if now - self.last_query_time > self.query_interval:
                self.send_status_query()
                self.last_query_time = now

            # 4. GC 관리
            gc_counter += 1
            if gc_counter >= 1000:
                gc.collect()
                gc_counter = 0

if __name__ == "__main__":
    controller = FM3Controller()
    controller.run()
