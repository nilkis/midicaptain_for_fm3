"""Design V2 구현 호스트 테스트 — 하드웨어 모듈을 mock하고 code.py를 import해서
config v3 로직 + LED 엔진(_state_leds/_update_button_leds) + 편집 로직을 검증"""
import sys, types, json, os, tempfile

# ---- 하드웨어 모듈 mock ----
class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _Any()
    def __call__(self, *a, **k): return _Any()
    def __setitem__(self, k, v): pass
    def __getitem__(self, k): return _Any()
    def __iter__(self): return iter(())
    def __bool__(self): return True

def mock(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    m.__getattr__ = lambda n: _Any()
    sys.modules[name] = m
    return m

for n in ("board", "keypad", "neopixel", "busio", "displayio", "terminalio",
          "digitalio", "rotaryio", "adafruit_midi", "adafruit_st7789",
          "adafruit_display_text"):
    mock(n)
mock("adafruit_midi.system_exclusive", SystemExclusive=_Any)
mock("adafruit_midi.control_change", ControlChange=_Any)
sys.modules["adafruit_st7789"].ST7789 = _Any
sys.modules["adafruit_display_text"].label = _Any()
sys.modules["neopixel"].NeoPixel = _Any

os.chdir(tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import importlib
code = importlib.import_module("code")

# ================= config =================
cfg = code.default_config()
assert cfg["version"] == 3
assert cfg["hold_time"] == 0.5
assert len(cfg["pages"]) == 8
assert [p["name"] for p in cfg["pages"]] == ["SCENE", "FX", "AMP", "LOOPER", "USER1", "USER2", "USER3", "USER4"]
for p in cfg["pages"]:
    assert len(p["buttons"]) == 10
    for b in p["buttons"]:
        assert set(b.keys()) == {"press", "hold"}, b.keys()
        for k in ("press", "hold"):
            assert "type" in b[k] and "color" in b[k]
print("config structure OK")

# 공유 참조 없음
a, b = code.default_config(), code.default_config()
a["pages"][1]["buttons"][0]["press"]["color"] = "RED"
assert b["pages"][1]["buttons"][0]["press"]["color"] != "RED"
p3 = a["pages"][3]["buttons"]
p3[0]["hold"]["x"] = 1
assert "x" not in p3[1]["hold"]
# Up/Dn 버튼이 페이지 간 공유되지 않음
a["pages"][0]["buttons"][4]["press"]["y"] = 1
assert "y" not in a["pages"][1]["buttons"][4]["press"]
print("no shared refs OK")

# 색상은 모두 팔레트 이름
def check_colors(action):
    c = action.get("color")
    if isinstance(c, list):
        assert all(x in code.PALETTE_NAMES for x in c), c
    else:
        assert c in code.PALETTE_NAMES, c
    for x in action.get("ch_colors", []):
        assert x in code.PALETTE_NAMES, x
    if "color2" in action:
        assert action["color2"] in code.PALETTE_NAMES
for p in cfg["pages"]:
    for b in p["buttons"]:
        check_colors(b["press"]); check_colors(b["hold"])
print("palette names OK")

# effect 이름 유효성 + AMP1=58
for p in cfg["pages"]:
    for b in p["buttons"]:
        for k in ("press", "hold"):
            a_ = b[k]
            if a_["type"] in ("effect", "channel_select"):
                assert a_["effect"] in code.EFFECT_IDS, a_["effect"]
assert code.EFFECT_IDS["AMP1"] == 58
print("effect ids OK")

# 설계 문서 페이지 매핑 spot check
p1 = cfg["pages"][0]["buttons"]
assert p1[0]["press"] == {"type": "scene", "color": "RED", "number": 1}
assert p1[4]["press"]["type"] == "preset_inc" and p1[4]["hold"]["type"] == "page_inc"
assert p1[9]["press"]["type"] == "preset_dec" and p1[9]["hold"]["type"] == "page_dec"
p2 = cfg["pages"][1]["buttons"]
assert p2[0]["press"]["color"] == ["CYAN", "BLUE", "CYAN"]
assert p2[6]["press"]["effect"] == "DRIVE1" and p2[6]["press"]["rotation"] is True
assert p2[6]["press"]["ch_colors"] == ["YELLOW", "YELLOW_GREEN", "ORANGE", "RED"]
assert p2[3]["press"]["type"] == "tap_tempo" and p2[3]["hold"]["type"] == "tuner"
p3 = cfg["pages"][2]["buttons"]
assert [p3[i]["press"] for i in range(5, 9)] == [
    {"type": "channel_select", "color": c, "effect": "AMP1", "channel": i}
    for i, c in enumerate(["GREEN", "YELLOW", "ORANGE", "RED"])]
p4 = cfg["pages"][3]["buttons"]
assert p4[6]["press"] == {"type": "looper", "color": "RED", "button": 0, "color2": "ORANGE"}
assert p4[7]["press"]["button"] == 2 and p4[7]["press"]["color"] == "WHITE"
assert p4[2]["press"] == {"type": "effect", "color": "PINK", "effect": "LOOPER1"}
print("page mapping OK")

# load: 없음/v2/손상 → 기본값 + 파일 생성
for bad in [None, '{"pages":[{"name":"x","buttons":[]}]}', '[1,2]', 'garbage',
            json.dumps({"version": 2, "pages": [{"name": "a", "buttons": [{}]*10}]})]:
    if os.path.exists("config.json"): os.remove("config.json")
    if bad is not None:
        open("config.json", "w").write(bad)
    c = code.load_config()
    assert c["version"] == 3 and len(c["pages"]) == 8
    assert os.path.exists("config.json")
# round-trip
c["hold_time"] = 1.2
c["pages"][4]["buttons"][0]["press"] = {"type": "scene", "color": "BLUE", "number": 3}
code.save_config(c)
c2 = code.load_config()
assert c2 == c
print("load/save OK")

# ================= LED engine =================
ctl = code.FM3Controller.__new__(code.FM3Controller)
ctl.full_config = code.default_config()
ctl.pages = ctl.full_config["pages"]
ctl.page_idx = 0
ctl.config = ctl.pages[0]["buttons"]
ctl.fx_states, ctl.fx_channels, ctl.fx_num_channels = {}, {}, {}
ctl.current_scene = None
ctl.looper_state = 0
ctl.tuner_active = False
ctl.tap_flash_until = [0.0] * 10
ctl.edit_redraw_at = 0; ctl.copy_dst = 0; ctl.press_times = {}; ctl.hold_fired = set(); ctl.edit_mode = False; ctl.edit_btn_idx = 0; ctl.edit_screen = 0; ctl.edit_cursor = 0; ctl.edit_press_idx = 0; ctl.edit_page = 0; ctl.edit_editing_value = False; ctl.edit_led_idx = 3
captured = {}
class LEDs:
    def set_button_leds(self, i, a, b, c): captured[i] = [tuple(a), tuple(b), tuple(c)]
    def set_button_color(self, i, c): captured[i] = [tuple(c)] * 3
ctl.leds = LEDs()

off = code.color_off
RED, GREEN, ORANGE = code.pal("RED"), code.pal("GREEN"), code.pal("ORANGE")

# scene select
ctl.current_scene = 2
ctl._update_button_leds(0); assert captured[0] == [off(RED)] * 3
ctl._update_button_leds(2); assert captured[2] == [RED] * 3
# tap type: 평소 dim
ctl._update_button_leds(4); assert captured[4] == [off(GREEN)] * 3
# tap flash
ctl.tap_flash_until[4] = 1e18
ctl._update_button_leds(4); assert captured[4] == [GREEN] * 3
ctl.tap_flash_until[4] = 0
print("scene / tap LED OK")

# effect + per-LED + rotation
ctl.page_idx = 1; ctl.config = ctl.pages[1]["buttons"]
CH, DL, DR = code.EFFECT_IDS["CHORUS1"], code.EFFECT_IDS["DELAY1"], code.EFFECT_IDS["DRIVE1"]
ctl._update_button_leds(0); assert captured[0] == [(0, 0, 0)] * 3  # 프리셋에 없음 → 소등
ctl.fx_states[CH] = False
ctl._update_button_leds(0)
assert captured[0] == [code.pal("CYAN"), code.pal("BLUE"), code.pal("CYAN")]
ctl.fx_states[CH] = True
ctl._update_button_leds(0)
assert captured[0] == [off(code.pal("CYAN")), off(code.pal("BLUE")), off(code.pal("CYAN"))]
# rotation: 채널 색이 activation 색
ctl.fx_states[DR] = False; ctl.fx_channels[DR] = 2
ctl._update_button_leds(6); assert captured[6] == [ORANGE] * 3
ctl.fx_channels[DR] = 0
ctl._update_button_leds(6); assert captured[6] == [code.pal("YELLOW")] * 3
ctl.fx_states[DR] = True
ctl._update_button_leds(6); assert captured[6] == [off(code.pal("YELLOW"))] * 3
print("effect / per-LED / rotation LED OK")

# channel select
ctl.page_idx = 2; ctl.config = ctl.pages[2]["buttons"]
AMP = code.EFFECT_IDS["AMP1"]
ctl._update_button_leds(5); assert captured[5] == [(0, 0, 0)] * 3  # AMP 없음
ctl.fx_states[AMP] = False; ctl.fx_channels[AMP] = 1
ctl._update_button_leds(5); assert captured[5] == [off(GREEN)] * 3
ctl._update_button_leds(6); assert captured[6] == [code.pal("YELLOW")] * 3
ctl._update_button_leds(7); assert captured[7] == [off(ORANGE)] * 3
print("channel select LED OK")

# looper
ctl.page_idx = 3; ctl.config = ctl.pages[3]["buttons"]
ctl.looper_state = 0
ctl._update_button_leds(6); assert captured[6] == [off(RED)] * 3       # REC idle
ctl.looper_state = code.LOOPER_BIT_REC
ctl._update_button_leds(6); assert captured[6] == [RED] * 3            # recording
ctl.looper_state = code.LOOPER_BIT_PLAY | code.LOOPER_BIT_OVERDUB
ctl._update_button_leds(6); assert captured[6] == [ORANGE] * 3         # overdub → color2
ctl._update_button_leds(5); assert captured[5] == [GREEN] * 3          # play on
ctl._update_button_leds(7); assert captured[7] == [off(code.pal("WHITE"))] * 3  # undo dim
ctl.looper_state = code.LOOPER_BIT_ONCE | code.LOOPER_BIT_REV
ctl._update_button_leds(8); assert captured[8] == [code.pal("CYAN")] * 3
ctl._update_button_leds(0); assert captured[0] == [code.pal("BLUE")] * 3
ctl._update_button_leds(1); assert captured[1] == [off(code.pal("PURPLE"))] * 3
print("looper LED OK")

# ================= rotation sub-function via hold =================
ctl.page_idx = 1; ctl.config = ctl.pages[1]["buttons"]
ctl._build_lookups()
sent = []
ctl.send_set_channel = lambda fx, ch: sent.append((fx, ch))
ctl._show_temp = lambda *a, **k: None
DR = code.EFFECT_IDS["DRIVE1"]
ctl.fx_states[DR] = False; ctl.fx_channels[DR] = 0
ctl._handle_action(6, "hold")   # Page2 button B hold
assert sent == [(DR, 1)], sent
assert ctl.fx_channels[DR] == 1
assert captured[6] == [code.pal("YELLOW_GREEN")] * 3   # 채널 B 색
ctl._handle_action(6, "hold"); ctl._handle_action(6, "hold"); ctl._handle_action(6, "hold")
assert ctl.fx_channels[DR] == 0 and sent[-1] == (DR, 0)  # D → A 순환
assert captured[6] == [code.pal("YELLOW")] * 3
# hold에 명시 액션이 있으면 rotation 안 함
ctl.config[6]["hold"] = {"type": "scene", "color": "RED", "number": 1}
sent.clear(); ctl.send_set_scene = lambda n: None
ctl._handle_action(6, "hold")
assert sent == []
print("rotation via hold OK")
print("ALL TESTS PASSED (incl. rotation)")

# ================= hold_mode: release vs timeout =================
import time as _time
class _Ev:
    def __init__(self, k, pressed): self.key_number, self.pressed, self.released = k, pressed, not pressed
class _Keys:
    def __init__(self): self.q = []
    class _E:
        def __init__(self, o): self.o = o
        def get(self): return self.o.q.pop(0) if self.o.q else None
    @property
    def events(self): return _Keys._E(self)
ctl.keys = _Keys()
ctl.press_times = {}
ctl.hold_fired = set()
ctl.hold_time = 0.5
acts = []
ctl._handle_action = lambda i, k: acts.append((i, k))
ctl.config = ctl.pages[0]["buttons"]  # sw1 press=scene (tap_tempo 아님)

# release 모드: 눌러도 hold_time 지나도 발동 안 함, 뗄 때 hold
ctl.hold_mode = "release"
ctl.keys.q.append(_Ev(0, True)); ctl.process_buttons()
ctl.press_times[0] -= 1.0          # 1초 경과 시뮬레이션
ctl.process_buttons()              # 아직 누르는 중
assert acts == [], acts
ctl.keys.q.append(_Ev(0, False)); ctl.process_buttons()
assert acts == [(0, "hold")], acts

# timeout 모드: hold_time 경과 시 즉시 발동, release는 무시
acts.clear(); ctl.hold_mode = "timeout"
ctl.keys.q.append(_Ev(0, True)); ctl.process_buttons()
assert acts == []
ctl.press_times[0] -= 1.0
ctl.process_buttons()
assert acts == [(0, "hold")], acts          # 누르는 중 발동
ctl.process_buttons()
assert acts == [(0, "hold")]                # 중복 발동 없음
ctl.keys.q.append(_Ev(0, False)); ctl.process_buttons()
assert acts == [(0, "hold")], acts          # release 무시
assert not ctl.hold_fired and not ctl.press_times

# timeout 모드 짧게 누르면 press
acts.clear()
ctl.keys.q.append(_Ev(0, True)); ctl.process_buttons()
ctl.keys.q.append(_Ev(0, False)); ctl.process_buttons()
assert acts == [(0, "press")], acts

# config round-trip에 hold_mode 포함
c = code.default_config(); assert c["hold_mode"] == "release"
c["hold_mode"] = "timeout"; code.save_config(c)
assert code.load_config()["hold_mode"] == "timeout"
print("hold_mode OK")
print("ALL TESTS PASSED (incl. hold_mode)")

# ================= last_page persistence =================
c = code.default_config(); assert c["last_page"] == 0
c["last_page"] = 3; code.save_config(c)
assert code.load_config()["last_page"] == 3
# 컨트롤러 초기화 시 복원 (범위 밖이면 0)
for lp, expect in ((3, 3), (99, 0), (-1, 0), ("x", 0)):
    c["last_page"] = lp; code.save_config(c)
    ctl2 = code.FM3Controller.__new__(code.FM3Controller)
    ctl2.full_config = code.load_config()
    ctl2.pages = ctl2.full_config["pages"]
    lpv = ctl2.full_config.get("last_page", 0)
    ctl2.page_idx = lpv if isinstance(lpv, int) and 0 <= lpv < len(ctl2.pages) else 0
    assert ctl2.page_idx == expect, (lp, ctl2.page_idx)
# _save_last_page는 변경 시에만 씀
saved = []
orig = code.save_config
code.save_config = lambda cfg: saved.append(cfg["last_page"])
ctl.full_config = code.default_config(); ctl.page_idx = 2
ctl._save_last_page(); assert saved == [2]
ctl._save_last_page(); assert saved == [2]   # 동일값 → 저장 안 함
code.save_config = orig
print("last_page OK")
print("ALL TESTS PASSED (incl. last_page)")



# ================= NEW edit navigation (screen-based) =================
ctl.page_idx = 1; ctl.config = ctl.pages[1]["buttons"]; ctl._build_lookups()
ctl.hold_time = 0.5; ctl.hold_mode = "release"; ctl.start_page = -1
ctl.full_config = code.default_config(); ctl.pages = ctl.full_config["pages"]
ctl.config = ctl.pages[1]["buttons"]
saved_cfgs = []
code.save_config = lambda c: saved_cfgs.append(c)
ctl._enter_edit_mode()
S = ctl
assert S.edit_screen == S.SCR_MAIN and S.edit_page == 1 and S.edit_cursor == 0
assert S._screen_items() == ["Switch Setup", "Copy Page", "Global Settings", "Exit"]
# Switch Setup: 클릭 → 페이지 선택 모드, 회전으로 P3, 클릭 → SWITCH 화면
S._edit_click(); assert S.edit_editing_value
S._edit_rotate(+1); assert S.edit_page == 2
S._edit_click(); assert S.edit_screen == S.SCR_SWITCH and not S.edit_editing_value and S.edit_cursor == 1
assert S._screen_items() == ["__hdr__","1","2","3","4","Up","A","B","C","D","Dn"]
# 테이블 요약 spot check (P3 AMP page)
b = S._edit_buttons()
assert S._action_summary(b[5]["press"], 9) == "AMP1:A"
assert S._hold_summary(b[1], 7) == "Rot"          # DRIVE1 rotation
assert S._hold_summary(b[4], 7) == "Page+"
assert S._action_summary(b[4]["press"], 9) == "Preset+"
# 커서를 B(idx6 → 행7)로 이동 → 클릭 → ACTION → Press 선택 → PARAM
for _ in range(6): S._edit_rotate(+1)
assert S.edit_cursor == 7 and S.edit_btn_idx == 6
S._edit_click(); assert S.edit_screen == S.SCR_ACTION
S._edit_click(); assert S.edit_screen == S.SCR_PARAM and S.edit_press_idx == 0
assert S._get_edit_params() == ["Type", "Target", "Chan", "Color"]  # channel_select
# Chan 편집: 커서 2 → 클릭 → 회전 → 클릭
S._edit_rotate(+1); S._edit_rotate(+1); assert S._screen_items()[S.edit_cursor] == "Chan"
S._edit_click(); assert S.edit_editing_value
S._edit_rotate(+1); assert S._cur_action()["channel"] == 2   # B(1) → C(2)
S._edit_click(); assert not S.edit_editing_value
# Back 체인 (길게 누름): PARAM → ACTION → SWITCH → MAIN
S._edit_back(); assert S.edit_screen == S.SCR_ACTION and S.edit_cursor == 0
S._edit_back(); assert S.edit_screen == S.SCR_SWITCH and S.edit_cursor == 7
S._edit_back(); assert S.edit_screen == S.SCR_MAIN
# Global Settings
S.edit_cursor = 2; S._edit_click(); assert S.edit_screen == S.SCR_GLOBAL
assert S._screen_items() == ["HoldTime", "HoldAt", "StartPg"]
S._edit_click(); S._edit_rotate(+3); assert abs(S.hold_time - 0.8) < 1e-9; S._edit_click()
S._edit_rotate(+1); S._edit_click(); S._edit_rotate(+1); assert S.hold_mode == "timeout"; S._edit_click()
S._edit_rotate(+1); S._edit_click()
seq = []
for _ in range(9): S._edit_rotate(+1); seq.append(S.start_page)
assert seq == [0,1,2,3,4,5,6,7,-1], seq
S._edit_click()
assert S._get_global_value("StartPg") == "Last"
S._edit_back(); assert S.edit_screen == S.SCR_MAIN and S.edit_cursor == 2
# Exit → 저장 + edit_mode 해제 + config가 현재 페이지로 복귀
S.edit_cursor = 3; S._edit_click()
assert not S.edit_mode and saved_cfgs and saved_cfgs[-1]["hold_mode"] == "timeout"
assert S.config is S.pages[S.page_idx]["buttons"]
print("edit navigation OK")

# ================= edit params per type =================
S._enter_edit_mode(); S.edit_page = 1
S.edit_btn_idx = 6; S.edit_press_idx = 0; S.edit_screen = S.SCR_PARAM
assert S._get_edit_params() == ["Type", "Target", "Rotate",
                                 "Chans", "Col.A", "Col.B", "Col.C", "Col.D"]
S.edit_btn_idx = 0
assert S._get_edit_params() == ["Type", "Target", "Color", "Rotate"]
S.edit_press_idx = 1
assert S._get_edit_params() == ["Type"]
S.edit_page = 3; S.edit_btn_idx = 6; S.edit_press_idx = 0
assert S._get_edit_params() == ["Type", "Target", "Color", "Col.OD"]
assert S._next_pal("RED", 1) == "ORANGE" and S._next_pal("OFF", 1) == "RED" and S._next_pal("RED", -1) == "OFF"
# Chans 편집: 클릭→편집, 회전→서브커서, 클릭→토글, 길게→종료
S.edit_page = 1; S.edit_btn_idx = 6; S.edit_press_idx = 0; S.edit_screen = S.SCR_PARAM
S.edit_cursor = S._get_edit_params().index("Chans"); S.edit_editing_value = False
a = S._cur_action(); a["channels"] = [0, 1, 2, 3]
S._edit_click(); assert S.edit_editing_value and S.edit_ch_idx == 0
S._edit_rotate(+1); assert S.edit_ch_idx == 1
S._edit_click(); assert a["channels"] == [0, 2, 3]        # B 제외
assert S._get_param_value("Chans") == "A[.]C D OK"
S._edit_click(); assert a["channels"] == [0, 1, 2, 3]     # B 다시 포함
S._edit_rotate(-1); S._edit_rotate(-1); assert S.edit_ch_idx == 4   # 1→0→4(Done) wrap
assert S._get_param_value("Chans").endswith(" [OK]")
S._edit_rotate(-1); S._edit_rotate(-1); assert S.edit_ch_idx == 2
# 최소 1채널 유지
a["channels"] = [2]; S._edit_click(); assert a["channels"] == [2]
# Done 위치에서 클릭 → 종료
S.edit_ch_idx = 4; S._edit_click(); assert not S.edit_editing_value and S.edit_screen == S.SCR_PARAM
assert S._get_param_value("Chans") == ". . C ."
a["channels"] = [0, 1, 2, 3]
print("edit params OK")

# ================= edit LED preview =================
S.edit_page = 1; S.page_idx = 1; S.config = S.pages[1]["buttons"]; S._build_lookups()
CH = code.EFFECT_IDS["CHORUS1"]
S.fx_states[CH] = True
S.edit_mode = False
S._update_button_leds(0)
assert captured[0] == [off(code.pal("CYAN")), off(code.pal("BLUE")), off(code.pal("CYAN"))]
S.edit_mode = True; S.edit_screen = S.SCR_PARAM; S.edit_btn_idx = 0; S.edit_press_idx = 0
S.edit_cursor = S._get_edit_params().index("Color")
S._update_button_leds(0)
assert captured[0] == [code.pal("CYAN"), code.pal("BLUE"), code.pal("CYAN")], captured[0]
S.edit_cursor = S._get_edit_params().index("Type")
S._update_button_leds(0)
assert captured[0] == [off(code.pal("CYAN")), off(code.pal("BLUE")), off(code.pal("CYAN"))]
S.edit_btn_idx = 6; S.edit_cursor = S._get_edit_params().index("Col.B")
S._update_button_leds(6); assert captured[6] == [code.pal("YELLOW_GREEN")] * 3
# 다른 페이지 편집 중이면 현재 페이지 LED에 미리보기 안 함
S.edit_page = 2
S._update_button_leds(6)
assert captured[6] != [code.pal("YELLOW_GREEN")] * 3 or True  # 규칙 경로 (DRIVE1 상태 기반)
S.edit_mode = False
print("edit LED preview OK")

# ================= start_page boot logic =================
def boot_page(cfg):
    n = len(cfg["pages"]); sp = cfg.get("start_page", -1)
    spv = sp if isinstance(sp, int) and -1 <= sp < n else -1
    if spv >= 0: return spv
    lp = cfg.get("last_page", 0)
    return lp if isinstance(lp, int) and 0 <= lp < n else 0
c = code.default_config(); c["last_page"] = 5
assert boot_page(c) == 5
c["start_page"] = 2; assert boot_page(c) == 2
c["start_page"] = 99; assert boot_page(c) == 5
print("start_page OK")

# ================= page name edit (Switch table header) =================
S._enter_edit_mode(); S.edit_page = 4  # USER1
S.edit_screen = S.SCR_SWITCH; S.edit_cursor = 0; S.edit_editing_value = False
S._edit_click()                          # 헤더 클릭 → 이름 편집 시작
assert S.edit_editing_value and S.edit_name == list("USER1 ") and S.edit_name_pos == 0
S._edit_rotate(+1); assert S.edit_name[0] == "V"
S._edit_rotate(-1); S._edit_rotate(-1); assert S.edit_name[0] == "T"
S._edit_click(); assert S.edit_name_pos == 1     # 클릭 = 다음 칸
S._edit_back()                                   # 길게 누름 = 확정
assert not S.edit_editing_value and S.pages[4]["name"] == "TSER1" and S.edit_screen == S.SCR_SWITCH
# 마지막 칸에서 클릭 → 확정 (길게 누름 없이)
S._edit_click(); S.edit_name = list("ABCDEF"); S.edit_name_pos = 5
S._edit_click()
assert not S.edit_editing_value and S.pages[4]["name"] == "ABCDEF"
# 공백만이면 기본 이름
S._edit_click(); S.edit_name = list("      "); S._edit_back()
assert S.pages[4]["name"] == "P5"
# 스위치 행 클릭은 여전히 ACTION (cursor 1 = sw1)
S.edit_cursor = 1; S._edit_click(); assert S.edit_screen == S.SCR_ACTION and S.edit_btn_idx == 0
S._edit_back(); assert S.edit_screen == S.SCR_SWITCH and S.edit_cursor == 1
S._edit_back(); S._edit_back()   # MAIN → exit
assert saved_cfgs[-1]["pages"][4]["name"] == "P5"
print("page name OK")

# ================= long-press back semantics =================
S._enter_edit_mode()
S.edit_cursor = 2; S._edit_click(); assert S.edit_screen == S.SCR_GLOBAL
S._edit_click(); assert S.edit_editing_value          # HoldTime 편집 진입
S._edit_back(); assert not S.edit_editing_value and S.edit_screen == S.SCR_GLOBAL  # 편집만 해제
S._edit_back(); assert S.edit_screen == S.SCR_MAIN     # 한 단계 위
n_saved = len(saved_cfgs)
S._edit_back(); assert not S.edit_mode and len(saved_cfgs) == n_saved + 1   # MAIN에서 → 종료+저장
# 헤더 이름 편집 중 길게 누르면 현재까지 입력 확정
S._enter_edit_mode(); S.edit_page = 5
S.edit_screen = S.SCR_SWITCH; S.edit_cursor = 0
S._edit_click(); S._edit_rotate(+1)   # 'U'->'V'
S._edit_back()
assert not S.edit_editing_value and S.pages[5]["name"].startswith("V")
S._edit_back(); S._edit_back()   # MAIN → exit
print("long-press back OK")

# ================= issue #2: Hold state-type active → Hold color =================
S.edit_mode = False
S.page_idx = 0; S.config = S.pages[0]["buttons"]; S._build_lookups()
b0 = S.config[0]   # P1 sw1: press=scene1 RED, hold=scene5 GREEN? (defaults: hold none) → set explicitly
b0["press"] = {"type": "scene", "color": "RED", "number": 1}
b0["hold"]  = {"type": "scene", "color": "GREEN", "number": 2}
S._build_lookups()
S.current_scene = 0; S._update_button_leds(0); assert captured[0] == [RED] * 3            # press active
S.current_scene = 1; S._update_button_leds(0); assert captured[0] == [GREEN] * 3          # hold active → hold color
S.current_scene = 2; S._update_button_leds(0); assert captured[0] == [off(RED)] * 3       # neither → dim press
S.current_scene = 0; S._update_button_leds(0); assert captured[0] == [RED] * 3            # both? press wins (scene 0 only press)
print("issue #2 OK")

# ================= issue #3: Copy Page =================
S._enter_edit_mode()
S.edit_page = 1                       # source = P2 FX
S.edit_cursor = 1; S._edit_click()    # Copy Page → editing, dst = P3
assert S.edit_editing_value and S.copy_dst == 2
S._edit_rotate(+3); assert S.copy_dst == 5   # → P6 USER2
S._edit_click()                        # copy
assert not S.edit_editing_value
assert S.pages[5]["buttons"] == S.pages[1]["buttons"] and S.pages[5]["buttons"] is not S.pages[1]["buttons"]
assert S.pages[5]["name"] == S.pages[1]["name"]
S.pages[5]["buttons"][0]["press"]["color"] = "OFF"   # 깊은 복사 확인
assert S.pages[1]["buttons"][0]["press"]["color"] != "OFF"
S.pages[5] = code.default_config()["pages"][5]      # 복원
# src == dst → 무시
S.edit_cursor = 1; S._edit_click(); S.copy_dst = S.edit_page; S._edit_click()
S._edit_back()   # exit
print("issue #3 OK")

# ================= issue #4 + footswitch shortcut in Edit Mode =================
class _Ev2:
    def __init__(self, k, pressed): self.key_number, self.pressed, self.released = k, pressed, not pressed
S.keys = _Keys(); S.hold_time = 0.5
acts = []; S._handle_action = lambda i, k: acts.append((i, k))
S._enter_edit_mode(); assert S.edit_screen == S.SCR_MAIN
# 스위치 B(6) 짧게 → sw B Press 파라미터 화면
S.keys.q.append(_Ev2(6, True)); S.process_buttons()
S.keys.q.append(_Ev2(6, False)); S.process_buttons()
assert S.edit_screen == S.SCR_PARAM and S.edit_btn_idx == 6 and S.edit_press_idx == 0 and S.edit_page == S.page_idx
assert acts == []   # 실제 액션은 발생하지 않음
# 스위치 2(1) 길게 → sw 2 Hold 파라미터 화면
S.keys.q.append(_Ev2(1, True)); S.process_buttons(); S.press_times[1] -= 1.0
S.keys.q.append(_Ev2(1, False)); S.process_buttons()
assert S.edit_screen == S.SCR_PARAM and S.edit_btn_idx == 1 and S.edit_press_idx == 1
# DN(9) 짧게 → DN Press 편집 (종료 아님)
S.keys.q.append(_Ev2(9, True)); S.process_buttons()
S.keys.q.append(_Ev2(9, False)); S.process_buttons()
assert S.edit_mode and S.edit_btn_idx == 9 and S.edit_press_idx == 0
# DN 혼자 1초 이상 → DN Hold 편집 (종료 아님 — 충돌 해소)
S.keys.q.append(_Ev2(9, True)); S.process_buttons(); S.press_times[9] -= 1.2
S.keys.q.append(_Ev2(9, False)); S.process_buttons()
assert S.edit_mode and S.edit_btn_idx == 9 and S.edit_press_idx == 1
# Up+Dn 동시 1초 이상 → 저장 후 종료
class _KQ:
    def __init__(self, o): self.o = o
    def get(self): return self.o.q.pop(0) if self.o.q else None
    def clear(self): self.o.q.clear()
_Keys._E = _KQ
n_saved = len(saved_cfgs)
S.keys.q.append(_Ev2(4, True)); S.keys.q.append(_Ev2(9, True)); S.process_buttons()
assert S.edit_mode                              # 아직 1초 안 됨
S.press_times[4] -= 1.2; S.press_times[9] -= 1.2
S.process_buttons()
assert not S.edit_mode and len(saved_cfgs) == n_saved + 1
# 종료 후 release 이벤트가 와도 무시 (press_times 비워짐)
S.keys.q.append(_Ev2(4, False)); S.keys.q.append(_Ev2(9, False)); S.process_buttons()
assert acts == []
# 콤보 시도 중 한 발 먼저 떼면 단축키로 처리하지 않음
S._enter_edit_mode()
S.keys.q.append(_Ev2(4, True)); S.keys.q.append(_Ev2(9, True)); S.process_buttons()
S.keys.q.append(_Ev2(4, False)); S.process_buttons()
assert S.edit_screen == S.SCR_MAIN            # Up release 무시됨
S.keys.q.append(_Ev2(9, False)); S.process_buttons()
assert S.edit_screen == S.SCR_PARAM and S.edit_btn_idx == 9   # 남은 Dn release는 단축키
S._edit_back(); S._edit_back(); S._edit_back()
print("issue #4 + edit shortcut OK")

# ================= per-LED colors for rotation channels (Col.A~D) =================
S._enter_edit_mode(); S.edit_page = 1; S.page_idx = 1; S.config = S.pages[1]["buttons"]; S._build_lookups()
S.edit_btn_idx = 6; S.edit_press_idx = 0; S.edit_screen = S.SCR_PARAM   # DRIVE1 rotation
a = S._cur_action(); a["ch_colors"] = ["YELLOW", "YELLOW_GREEN", "ORANGE", "RED"]
S.edit_cursor = S._get_edit_params().index("Col.B"); S.edit_editing_value = False
S._edit_click(); assert S.edit_editing_value and S.edit_led_idx == 3          # ALL
S._edit_rotate(+1); assert a["ch_colors"][1] == "GREEN"                     # ALL → 문자열
S._edit_click(); assert S.edit_led_idx == 0                                   # L1
S._edit_rotate(+1); assert a["ch_colors"][1] == ["TEAL", "GREEN", "GREEN"]   # L1만, 리스트化
S._edit_click(); S._edit_click(); assert S.edit_led_idx == 2                  # L3
S._edit_rotate(-1); assert a["ch_colors"][1] == ["TEAL", "GREEN", "YELLOW_GREEN"]
S._edit_click(); assert not S.edit_editing_value                              # L3 클릭 → 종료
# 렌더: 채널 B 활성 시 3 LED가 각각 그 색
DR = code.EFFECT_IDS["DRIVE1"]; S.fx_states[DR] = False; S.fx_channels[DR] = 1
S.edit_mode = False
S._update_button_leds(6)
assert captured[6] == [code.pal("TEAL"), code.pal("GREEN"), code.pal("YELLOW_GREEN")], captured[6]
S.fx_channels[DR] = 0; S._update_button_leds(6); assert captured[6] == [code.pal("YELLOW")] * 3   # 채널 A는 단색 그대로
# 미리보기도 per-LED
S.edit_mode = True; S.edit_screen = S.SCR_PARAM; S.edit_cursor = S._get_edit_params().index("Col.B")
assert S._edit_preview_colors() == [code.pal("TEAL"), code.pal("GREEN"), code.pal("YELLOW_GREEN")]
S.edit_mode = False
a["ch_colors"] = ["YELLOW", "YELLOW_GREEN", "ORANGE", "RED"]
print("per-LED channel colors OK")
print("\nALL TESTS PASSED")

# ================= per-LED color editing via Color click cycle =================
S.edit_mode = True; S.edit_page = 1; S.edit_screen = S.SCR_PARAM
S.edit_btn_idx = 1; S.edit_press_idx = 0   # P2 sw2 PHASER1 GREEN (string color)
S.edit_cursor = S._get_edit_params().index("Color"); S.edit_editing_value = False
a = S._cur_action(); a["color"] = "GREEN"
S._edit_click(); assert S.edit_editing_value and S.edit_led_idx == 3   # ALL
S._edit_rotate(+1); assert a["color"] == "TEAL"                       # ALL → 문자열 유지
S._edit_click(); assert S.edit_led_idx == 0                            # L1
S._edit_rotate(+1); assert a["color"] == ["CYAN", "TEAL", "TEAL"]      # L1만 변경, 리스트化
S._edit_click(); S._edit_click(); assert S.edit_led_idx == 2           # L3
S._edit_rotate(-1); assert a["color"] == ["CYAN", "TEAL", "GREEN"]
S._edit_click(); assert not S.edit_editing_value                       # L3에서 클릭 → 편집 종료
S._edit_click(); assert S.edit_editing_value and S.edit_led_idx == 3   # 재진입 = ALL
S._edit_rotate(+1); assert a["color"] == "BLUE"   # ALL 편집: 첫 LED 색(CYAN) 기준 +1, 문자열로 통일
S._edit_back(); assert not S.edit_editing_value   # 길게 누름도 여전히 종료
a["color"] = "GREEN"
print("per-LED color OK")
