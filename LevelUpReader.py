#!/usr/bin/env python3
"""
遍历阅读 LevelUpReader
========================
TXT 小说阅读器，支持 edge-tts 多角色语音朗读。
角色自动识别、男女声音分配、高级朗读模式。
Windows XP 经典风格界面，沉浸式精简模式。

作者: OpenCode AI
版本: 1.0
协议: MIT License
"""

import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"

import asyncio
import hashlib
import json
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

import pygame
import edge_tts
import chardet

# ========== Constants ==========
APP_NAME = "遍历阅读"
APP_DIR = Path(os.getenv('APPDATA', str(Path.home()))) / "LevelUpReader"
BOOKMARKS_DIR = APP_DIR / "bookmarks"
CONFIG_FILE = APP_DIR / "config.json"

CHINESE_VOICES = [
    ("zh-CN-XiaoxiaoNeural",   "晓晓 (女)"),
    ("zh-CN-YunyangNeural",    "云扬 (男)"),
    ("zh-CN-YunxiNeural",      "云希 (男)"),
    ("zh-CN-XiaoyiNeural",     "晓伊 (女)"),
    ("zh-CN-YunjianNeural",    "云健 (男)"),
    ("zh-CN-XiaoxuanNeural",   "晓萱 (女)"),
]

MALE_VOICES = ["zh-CN-YunyangNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural"]
FEMALE_VOICES = ["zh-CN-XiaoyiNeural", "zh-CN-XiaoxuanNeural"]

VOICE_ID_TO_NAME = {v[0]: v[1] for v in CHINESE_VOICES}
VOICE_NAMES = [v[1] for v in CHINESE_VOICES]

# XP classic colors
XP_BG = "SystemButtonFace"
XP_FG = "SystemButtonText"
XP_BLUE = "#316AC5"


# ========== Utility Functions ==========

def load_file_with_encoding(filepath):
    with open(filepath, 'rb') as f:
        raw = f.read()

    if not raw:
        return ""

    # BOM detection
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode('utf-8-sig')
    if raw[:2] == b'\xff\xfe':
        return raw[2:].decode('utf-16-le')
    if raw[:2] == b'\xfe\xff':
        return raw[2:].decode('utf-16-be')
    if raw[:4] == b'\xff\xfe\x00\x00':
        return raw[4:].decode('utf-32-le')
    if raw[:4] == b'\x00\x00\xfe\xff':
        return raw[4:].decode('utf-32-be')

    # Try UTF-8 first (most common)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass

    # Use chardet
    result = chardet.detect(raw)
    encoding = result.get('encoding', '')
    confidence = result.get('confidence', 0)

    # Chinese encodings to try (ordered by commonality)
    cn_encodings = ['gb18030', 'gbk', 'gb2312', 'big5', 'big5hkscs']

    # If chardet suggests a Chinese encoding with high confidence, try it first
    if encoding and encoding.lower() in ('gb2312', 'gbk', 'gb18030', 'big5', 'euc-cn', 'euc-tw'):
        cn_encodings.insert(0, encoding)
        if encoding.lower() in cn_encodings[1:]:
            cn_encodings.remove(encoding)

    for enc in cn_encodings:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # If chardet found something else, try it
    if encoding and encoding.lower() not in ('ascii', 'iso-8859-1', 'windows-1252'):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass

    # Last resort
    for enc in ['latin-1', 'cp1252', 'shift_jis', 'euc-jp', 'euc-kr']:
        try:
            decoded = raw.decode(enc)
            # Check if result looks like Chinese text
            if any('\u4e00' <= c <= '\u9fff' for c in decoded[:500]):
                return decoded
        except (UnicodeDecodeError, LookupError):
            continue

    return raw.decode('utf-8', errors='replace')


def split_chapters(text):
    pattern = re.compile(
        r'(?:^|\n)\s*'
        r'(第[零一二三四五六七八九十百千万0-9]+[章节卷部回集]|'
        r'Chapter\s+\d+|CHAPTER\s+\d+|'
        r'第[0-9]+[章节回])\s*'
        r'(?:[^\n]*\n|$)',
        re.MULTILINE
    )
    matches = list(pattern.finditer(text))
    if not matches:
        lines = text.strip().split('\n')
        first_line = lines[0].strip() if lines else "全文"
        return [(first_line, text)]
    chapters = []
    if matches[0].start() > 0:
        prefix = text[:matches[0].start()].strip()
        if prefix:
            chapters.append(("前言", prefix))
    for i, match in enumerate(matches):
        start = match.start()
        title = match.group().strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end]
        chapters.append((title, content))
    return chapters


def split_sentences(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    parts = re.split(r'(?<=[。！？；!?;])', text)
    raw = [p.strip() for p in parts if p.strip()]
    merged = []
    for s in raw:
        if merged and re.match(r'^[。！？；!?;]+$', s):
            merged[-1] += s
        else:
            merged.append(s)
    return merged if merged else [text.strip()]


def get_bookmark_path(filepath):
    file_hash = hashlib.md5(filepath.encode('utf-8')).hexdigest()
    return BOOKMARKS_DIR / f"{file_hash}.json"


def ensure_dirs():
    BOOKMARKS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


# ========== Character Analyzer ==========

class CharacterAnalyzer:
    _QUOTES = '"\u201c\u201d\u300c\u300d\u300e\u300f\uff02'
    DIALOGUE_RE = re.compile(rf'[{_QUOTES}](.+?)[{_QUOTES}]')
    SPEAKER_RE = re.compile(
        r'([\u4e00-\u9fff]{2,4}?)'
        r'(?:说道|笑道|问道|怒道|叹道|喝道|叫道|喊道|大喊|大喊大叫|'
        r'自言自语|说|道|问|喊|叫|答|讲|曰)')

    @classmethod
    def analyze(cls, text):
        from collections import Counter
        _bad_ending = set('地了的着过得都很也不就还又把被让从在对向与和或而因但所如果虽然然则之')
        _common_surname = set('王李张刘陈杨赵黄周吴徐孙马胡朱郭何林高罗郑梁谢宋唐韩冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文')
        name_counts = Counter()
        for m in cls.DIALOGUE_RE.finditer(text):
            start, end = m.start(), m.end()
            before = text[max(0, start - 40):start]
            after = text[end:end + 20]
            found = None
            for s in cls.SPEAKER_RE.finditer(before):
                found = s.group(1)
            if not found:
                m2 = cls.SPEAKER_RE.search(after)
                if m2:
                    found = m2.group(1)
            if found and len(found) >= 2 and not found.isdigit() and found[-1] not in _bad_ending:
                if len(found) > 2 and found[0] not in _common_surname:
                    continue
                name_counts[found] += 1
        return sorted([n for n, c in name_counts.items() if c >= 1])


class ScriptParser:
    DIALOGUE_RE = CharacterAnalyzer.DIALOGUE_RE
    _SPEECH_VERBS = r'(?:.|\n){0,15}(?:说道|笑道|问道|怒道|叹道|喝道|叫道|喊道|大喊|'
    _SPEECH_VERBS += r'说|道|问|喊|叫|答|讲|曰)'

    @classmethod
    def parse(cls, text, character_voices, narration_voice):
        segments = []
        pos = 0
        active_speaker = None
        char_names = sorted(character_voices.keys(), key=len, reverse=True)

        for m in cls.DIALOGUE_RE.finditer(text):
            if m.start() > pos:
                nar = text[pos:m.start()]
                if nar.strip():
                    segments.append(("旁白", nar, narration_voice, pos, m.start()))
                    for name in char_names:
                        if name in nar[-40:]:
                            active_speaker = name
                            break

            dialogue = m.group(1)
            start, end = m.start(), m.end()
            before = text[max(0, start - 20):start]
            after = text[end:end + 20]
            speaker = None

            # Tier 1: Speech verb right after dialogue (most reliable)
            for name in char_names:
                pat = re.compile(re.escape(name) + cls._SPEECH_VERBS)
                if pat.search(after):
                    speaker = name
                    break

            # Tier 2: Speech verb before dialogue
            if not speaker:
                for name in char_names:
                    pat = re.compile(re.escape(name) + cls._SPEECH_VERBS)
                    if pat.search(before):
                        speaker = name
                        break

            # Tier 3: Character name near dialogue (without speech verb)
            if not speaker:
                for name in char_names:
                    if name in before[-8:] or name in after[:8]:
                        speaker = name
                        break

            # Tier 4: Active speaker from narration context
            if not speaker:
                speaker = active_speaker

            if speaker and speaker in character_voices:
                voice = character_voices[speaker]
                active_speaker = speaker
            else:
                voice = narration_voice
                speaker = "旁白"

            segments.append((speaker, dialogue, voice, start, end))
            pos = end

        if pos < len(text):
            segments.append(("旁白", text[pos:], narration_voice, pos, len(text)))

        return segments


# ========== Config Manager ==========

class ConfigManager:
    DEFAULTS = {
        "voice": "zh-CN-XiaoxiaoNeural",
        "speed": 1.0,
        "window_geometry": "550x300",
        "font_size": 14,
    }

    def __init__(self):
        ensure_dirs()
        self.data = dict(self.DEFAULTS)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                self.data.update(saved)
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()


# ========== Bookmark Manager ==========

class BookmarkManager:
    @staticmethod
    def save(filepath, chapter_index, scroll_pos,
             characters=None, narration_voice=None):
        ensure_dirs()
        bookmark = {
            "filepath": filepath,
            "chapter_index": chapter_index,
            "scroll_pos": scroll_pos,
            "timestamp": time.time(),
        }
        if characters is not None:
            bookmark["characters"] = characters
        if narration_voice is not None:
            bookmark["narration_voice"] = narration_voice
        path = get_bookmark_path(filepath)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(bookmark, f, indent=2, ensure_ascii=False)

    @staticmethod
    def load(filepath):
        path = get_bookmark_path(filepath)
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get("filepath") == filepath:
                return data
        except (json.JSONDecodeError, IOError):
            pass
        return None

    @staticmethod
    def save_characters(filepath, characters, narration_voice):
        data = BookmarkManager.load(filepath) or {}
        data["filepath"] = filepath
        data["characters"] = characters
        data["narration_voice"] = narration_voice
        path = get_bookmark_path(filepath)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ========== TTS Worker ==========

class TTSWorker:
    def __init__(self, voice="zh-CN-XiaoxiaoNeural", speed=1.0):
        self.voice = voice
        self.speed = speed
        self._queue = queue.Queue()
        self._running = False
        self._paused = False
        self._stop_flag = False
        self._thread = None
        self._current_index = -1
        self._total_count = 0
        self._temp_dir = tempfile.mkdtemp(prefix="novel_reader_")
        self._on_sentence = None
        self._on_status = None
        try:
            pygame.mixer.init()
            self._audio_ok = True
        except pygame.error:
            self._audio_ok = False

    def set_callbacks(self, on_sentence=None, on_status=None):
        self._on_sentence = on_sentence
        self._on_status = on_status

    def speak(self, text, start_index=0):
        self.stop()
        self._stop_flag = False
        self._queue.put(("speak", text, start_index))
        self._ensure_thread()

    def speak_segments(self, segments):
        self.stop()
        self._stop_flag = False
        self._queue.put(("segments", segments))
        self._ensure_thread()

    def stop(self):
        self._stop_flag = True
        self._paused = False
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except pygame.error:
            pass

    def pause(self):
        self._paused = True
        try:
            pygame.mixer.music.pause()
        except pygame.error:
            pass
        self._emit_status("已暂停")

    def resume(self):
        self._paused = False
        try:
            pygame.mixer.music.unpause()
        except pygame.error:
            pass
        self._emit_status("播放中")

    @property
    def is_playing(self):
        busy = False
        try:
            busy = pygame.mixer.music.get_busy()
        except pygame.error:
            pass
        return self._running and not self._paused and busy

    @property
    def is_paused(self):
        return self._paused

    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _emit_sentence(self, index):
        self._current_index = index
        if self._on_sentence:
            self._on_sentence(index)

    def _emit_status(self, msg):
        if self._on_status:
            self._on_status(msg)

    def _clear_temp(self):
        for f in os.listdir(self._temp_dir):
            try:
                os.remove(os.path.join(self._temp_dir, f))
            except Exception:
                pass

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self._running:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            cmd = item[0]
            try:
                if cmd == "speak":
                    _, text, start_idx = item
                    self._speak_text(loop, text, start_idx)
                elif cmd == "segments":
                    self._speak_segments(loop, item[1])
            except Exception as e:
                print(f"[TTS] run error: {e}")
                self._emit_status(f"错误: {e}")
        loop.close()

    def _speak_text(self, loop, text, start_index=0):
        sentences = split_sentences(text)
        if not sentences:
            self._emit_status("没有可朗读的内容")
            return
        self._total_count = len(sentences)
        start_index = max(0, min(start_index, self._total_count - 1))
        self._clear_temp()
        if not self._audio_ok:
            self._emit_status("音频设备不可用")
            return
        for i in range(start_index, self._total_count):
            if self._stop_flag:
                break
            while self._paused and not self._stop_flag:
                time.sleep(0.1)
            if self._stop_flag:
                break
            sentence = sentences[i]
            if not sentence.strip():
                continue
            self._emit_sentence(i)
            self._emit_status(f"生成语音... ({i + 1}/{self._total_count})")
            mp3_path = os.path.join(self._temp_dir, f"s_{i}.mp3")
            try:
                loop.run_until_complete(self._gen(sentence, mp3_path))
            except Exception as e:
                print(f"[TTS] generate error: {e}")
                mp3_path = None
            if self._stop_flag:
                break
            if mp3_path and os.path.exists(mp3_path):
                try:
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.play()
                except pygame.error as e:
                    print(f"[TTS] play error: {e}")
                    continue
                self._emit_status(f"播放中 ({i + 1}/{self._total_count})")
                while not self._stop_flag:
                    try:
                        busy = pygame.mixer.music.get_busy()
                    except pygame.error:
                        break
                    if not busy:
                        break
                    time.sleep(0.05)
                if self._paused and not self._stop_flag:
                    while self._paused and not self._stop_flag:
                        time.sleep(0.1)
                    try:
                        pygame.mixer.music.unpause()
                    except pygame.error:
                        pass
        if not self._stop_flag:
            self._emit_sentence(-1)
            self._emit_status("播放完成")
        else:
            self._emit_sentence(-1)
            self._emit_status("已停止")

    def _speak_segments(self, loop, segments):
        self._total_count = len(segments)
        self._clear_temp()
        for i, (speaker, text, voice, start_pos, end_pos) in enumerate(segments):
            if self._stop_flag:
                break
            while self._paused and not self._stop_flag:
                time.sleep(0.1)
            if self._stop_flag:
                break
            if not text.strip():
                continue
            self._emit_sentence(i)
            vname = VOICE_ID_TO_NAME.get(voice, voice[-6:])
            self._emit_status(f"[{speaker}/{vname}] 生成... ({i + 1}/{self._total_count})")
            mp3_path = os.path.join(self._temp_dir, f"as_{i}.mp3")
            try:
                rate_str = f"{int((self.speed - 1.0) * 100):+d}%"
                tts_text = text[:800]
                loop.run_until_complete(
                    self._gen_with_voice(tts_text, voice, rate_str, mp3_path))
            except Exception as e:
                print(f"[TTS] adv error: {e}")
                self._emit_status(f"!{speaker} 生成失败")
                mp3_path = None
            if self._stop_flag:
                break
            if mp3_path and os.path.exists(mp3_path):
                try:
                    sz = os.path.getsize(mp3_path)
                    if sz < 200:
                        print(f"[ADV] skip tiny audio {sz}B speaker={speaker}")
                        self._emit_status(f"!{speaker} 音频过小({sz}B)")
                        continue
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.play()
                except pygame.error as e:
                    print(f"[TTS] play error: {e}")
                    continue
                vname = VOICE_ID_TO_NAME.get(voice, voice[-6:])
                self._emit_status(f"[{speaker}/{vname}] ({i + 1}/{self._total_count})")
                while not self._stop_flag:
                    try:
                        busy = pygame.mixer.music.get_busy()
                    except pygame.error:
                        break
                    if not busy:
                        break
                    time.sleep(0.05)
                if self._paused and not self._stop_flag:
                    while self._paused and not self._stop_flag:
                        time.sleep(0.1)
                    try:
                        pygame.mixer.music.unpause()
                    except pygame.error:
                        pass
        if not self._stop_flag:
            self._emit_sentence(-1)
            self._emit_status("播放完成")
        else:
            self._emit_sentence(-1)
            self._emit_status("已停止")

    async def _gen_with_voice(self, text, voice, rate_str, output_path):
        communicate = edge_tts.Communicate(text, voice, rate=rate_str)
        await communicate.save(output_path)

    async def _gen(self, text, output_path):
        rate_str = f"{int((self.speed - 1.0) * 100):+d}%"
        communicate = edge_tts.Communicate(text, self.voice, rate=rate_str)
        await communicate.save(output_path)

    def cleanup(self):
        self._running = False
        self.stop()
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except pygame.error:
            pass


# ========== Icon Generator ==========

_LOGO_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IB2cksfwAAAARnQU1BAACxjwv"
    "8YQUAAAAgY0hSTQAAeiYAAICEAAD6AAAAgOgAAHUwAADqYAAAOpgAABdwnLpRPAAAAAlwSFlzAAALEw"
    "AACxMBAJqcGAAAAAd0SU1FB+oGEgITJFAzid0AAASDSURBVFjD7ZddiFVVFMd/+9xzZ5w7TTqlqTXk"
    "QxSNQoVhSiaFhkhWNNRLRVApjkmWVEoQFWRURH5EvRT6IBHVS/kQmH3Qw1A0Fgwzk46i41fOHWacu"
    "XO/zrnnnP2xerjXN6vrqA1BC/bTOqz13+t31tp7K1BMnQlqagWAxxTbFAv4ryOw5V1rTfaljRepQU"
    "1qmfwba+3QM84eu8/ZwXs6JxeHySGwpZeXEUb7JDiaUS4BlxicXpG6+eeuy47AlbbMI3KfERzOgEW"
    "JgIiPky9N78J5l1WAzT+bcRFfUT5yLQKeMwhS85qrlZK9dnBF5kK6oG4Bvu8h7spPVPjHbYJBlIf"
    "gVylWqwBibiUo7PE8r+7/r24B8YnVmyhmO/T4MNHJUZQATsA5xBrCU3lMvgJx/uHyvpueq3tj9X4"
    "YdXcP5A4VssUhM5yeLZkbnljZLuJAHCqOOPTp4HFgonWuPyvTMu23ehHULaD5kdH9QBvA6J7MR0A"
    "7IihnwBmuus7/4cZ3dSdoqusSIfB9HzO88UPTd++Ocx2bmTm9toEErAZnaZreXA2pFGZwzfumZ+l"
    "u309dfBdEZ9a9QPHIBtWYel533b4eoLG1FWyMsgk4DTahqTUNQNJ3f6c627+R0sBT0S/tmy+qC8z"
    "YhuWUTr6HOMDDm9G6TX87/y6vwasl1mBisJqGppjkpzuWqmBsG/FxlIAKRt8xPYuWTRqBHuh7Mx"
    "gcDSCFxJax37O5wlD4mkpGwES1KkTgYsxoieKxeOto78ExkVngIHcizoUDA69eMIJkaM0S3f/gli"
    "uXd9/ZMmfGi0oArRnee2jr3HVDKyW2YBNUEiCmgriEUiVFW+fA8vHuwlv4LWChudl7ZeaT0Up7uG"
    "OT6V20uK4uSEY2zUzluj9HguuD79tP4TtEDBDTcE0KrTXitaBMBWcikAQxDr+5mSgq0DLHQ3SIco"
    "J4EcGBpY8y3r9dufzp+NdbFjYu6sv9LYLk4I8P6eLZIJ4IqYxPPICbQOkyoivQcO4Ec4iJwcV4J"
    "kGZ6mQE8KYplLFgHCmjyfcNPh6PjaBLcVg5k+34RwRXLO/dlW5IdRFWyB5QT+MiSIoQ5/GVqwpw"
    "GmWriZ0RnDZ4NZ+1go1DRFucgeGj/uuiE9LpVNeMjrHd9Q2ipAQ2ItPQJCRl0BZJDLo2X6yxeMog"
    "xoKtTkOn42pIA6IDnBPQisZpTjwR0Oa8XfAXAkJcHKODRiQpgvbAWEzsago0KIOyFiwoC85UfeIE"
    "V4nAs9jEgRWcVoh29Z8Fthhiys4ph0M8sA4xhiSoFU5rUA4MIII4h46qCSplcImGtGATh63YnA40"
    "Cid1nwUNq+P1wHoYpnJ3ev/ZM+rjuCRxONG4D2JMRYcpTwAPEYc1QlBQZYDxEfanj5sPlE9jJpX+"
    "Zv7b2dNfrJrtPfbd2HkRTOpKdmKrzCuMpDZHsUuLgK4QhoX0jlVf69OTuJH+/zSbWvP/rUQ7d25n"
    "wYIFjI/nyOcnaGtro6enh1QVgZznsSJcSt/ixUsIgoBsdhjf95mYyNHf38+ffomwQclE4g0AAAAA"
    "SUVORK5CYII="
)


def _load_icon_png():
    import base64
    return base64.b64decode(_LOGO_B64)


# ========== Main Application (Windows Classic Style) ==========

class NovelReaderApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.config = ConfigManager()
        self.title(APP_NAME)
        self.geometry(self.config.get("window_geometry", "550x300"))
        self.minsize(200, 120)
        self.configure(bg=XP_BG)
        self._set_icon()

        self.current_file = None
        self.chapters = []
        self.current_chapter_idx = -1
        self.chapter_text = ""
        self.sentence_offsets = []
        self._chapter_labels = []
        self._adv_segments = []
        self._adv_char_voices = {}
        self._adv_narration = "zh-CN-XiaoxiaoNeural"

        self.tts = TTSWorker(
            voice=self.config.get("voice", "zh-CN-XiaoxiaoNeural"),
            speed=self.config.get("speed", 1.0),
        )
        self.tts.set_callbacks(
            on_sentence=self._on_tts_sentence,
            on_status=self._on_tts_status,
        )

        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_welcome()
        self._disable_ime()
        self.bind("<FocusIn>", lambda e: self._disable_ime())

    def _disable_ime(self):
        try:
            import ctypes
            ctypes.windll.imm32.ImmAssociateContext(self.winfo_id(), 0)
        except Exception:
            pass

    # ---- UI Construction ----

    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        self._build_toolbar()
        self._build_search_bar()
        self._build_chapter_panel()
        self._build_text_area()
        self._build_info_bar()

    def _build_toolbar(self):
        self._toolbar = tk.Frame(self, bg=XP_BG, bd=2, relief="raised")
        self._toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0, pady=0)

        # Group 1: File
        tk.Button(self._toolbar, text="打开", width=4, relief="raised", bd=2,
                  command=self._open_file).pack(side="left", padx=(4, 1), pady=2)
        tk.Button(self._toolbar, text="目录", width=4, relief="raised", bd=2,
                  command=self._toggle_chapter_panel).pack(side="left", padx=1, pady=2)
        tk.Button(self._toolbar, text="搜索", width=4, relief="raised", bd=2,
                  command=self._toggle_search).pack(side="left", padx=1, pady=2)

        tk.Frame(self._toolbar, width=1, bg="gray", relief="sunken").pack(side="left", padx=(6, 4), fill="y")

        # Group 2: Playback
        self.btn_prev = tk.Button(self._toolbar, text="上一章", width=5, relief="raised", bd=2,
                                  command=self._prev_chapter)
        self.btn_prev.pack(side="left", padx=1, pady=2)
        self.btn_play = tk.Button(self._toolbar, text="播放", width=4, relief="raised", bd=2,
                                  command=self._toggle_play)
        self.btn_play.pack(side="left", padx=1, pady=2)
        self.btn_adv = tk.Button(self._toolbar, text="高级播放", width=7, relief="raised", bd=2,
                                  command=self._toggle_advanced)
        self.btn_adv.pack(side="left", padx=1, pady=2)
        self.btn_stop = tk.Button(self._toolbar, text="停止", width=4, relief="raised", bd=2,
                                  command=self._stop)
        self.btn_stop.pack(side="left", padx=1, pady=2)
        self.btn_next = tk.Button(self._toolbar, text="下一章", width=5, relief="raised", bd=2,
                                  command=self._next_chapter)
        self.btn_next.pack(side="left", padx=1, pady=2)

        tk.Frame(self._toolbar, width=1, bg="gray", relief="sunken").pack(side="left", padx=(6, 4), fill="y")

        # Info labels
        self.file_label = tk.Label(self._toolbar, text="未打开文件", bg=XP_BG, anchor="w",
                                   font=("", 8))
        self.file_label.pack(side="left", padx=2, pady=2)
        self.chapter_info = tk.Label(self._toolbar, text="", bg=XP_BG, anchor="w",
                                     font=("", 8))
        self.chapter_info.pack(side="left", padx=2, pady=2)

        # === Right side ===
        # Minimal mode toggle
        tk.Button(self._toolbar, text="精简", width=4, relief="raised", bd=2,
                  command=self._toggle_minimal).pack(side="right", padx=(4, 2), pady=2)

        # Font size
        tk.Button(self._toolbar, text="-", width=2, relief="raised", bd=2,
                  command=self._font_smaller).pack(side="right", padx=1, pady=2)
        tk.Button(self._toolbar, text="+", width=2, relief="raised", bd=2,
                  command=self._font_larger).pack(side="right", padx=1, pady=2)
        tk.Label(self._toolbar, text="A", bg=XP_BG, font=("", 8, "bold")).pack(side="right", padx=(4, 1), pady=2)

        # Speed
        self.speed_label = tk.Label(self._toolbar, text="1.0x", bg=XP_BG, width=4, font=("", 8))
        self.speed_label.pack(side="right", padx=2, pady=2)
        self.speed_var = tk.DoubleVar(value=self.config.get("speed", 1.0))
        speed_sc = tk.Scale(self._toolbar, from_=0.5, to=2.0, resolution=0.1,
                            variable=self.speed_var, orient="horizontal",
                            length=60, showvalue=0, bg=XP_BG, bd=1,
                            highlightthickness=0,
                            command=self._on_speed_change)
        speed_sc.pack(side="right", padx=2, pady=2)
        tk.Label(self._toolbar, text="语速", bg=XP_BG, font=("", 8)).pack(side="right", padx=(2, 0), pady=2)

        # Voice
        display = VOICE_ID_TO_NAME.get(
            self.config.get("voice", "zh-CN-XiaoxiaoNeural"), "晓晓 (女)")
        self.voice_var = tk.StringVar(value=display)
        voice_cb = ttk.Combobox(self._toolbar, textvariable=self.voice_var,
                                values=VOICE_NAMES, state="readonly", width=11)
        voice_cb.pack(side="right", padx=(4, 6), pady=4)
        voice_cb.bind("<<ComboboxSelected>>", self._on_voice_change)

    def _build_search_bar(self):
        bar = tk.Frame(self, bg=XP_BG, bd=1, relief="groove")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=0)
        bar.grid_remove()

        self._search_entry = tk.Entry(bar, width=18, font=("", 10))
        self._search_entry.pack(side="left", padx=(4, 2), pady=3)

        tk.Button(bar, text="上一个", width=4, relief="raised", bd=2,
                  command=self._search_prev).pack(side="left", padx=1, pady=3)
        tk.Button(bar, text="下一个", width=4, relief="raised", bd=2,
                  command=self._search_next).pack(side="left", padx=1, pady=3)

        self._search_count = tk.Label(bar, text="", bg=XP_BG, font=("", 9))
        self._search_count.pack(side="left", padx=4, pady=3)

        tk.Button(bar, text="x", width=2, relief="raised", bd=2,
                  command=self._toggle_search).pack(side="right", padx=(2, 4), pady=3)

        self._search_entry.bind("<Return>", lambda e: self._search_next())
        self._search_entry.bind("<KeyRelease>", self._on_search_typing)

        self._search_matches = []
        self._search_index = -1
        self._search_bar = bar

    def _build_chapter_panel(self):
        self._chapter_visible = False
        self._chapter_panel = tk.Frame(self, bg=XP_BG, bd=2, relief="groove", width=130)
        self._chapter_panel.grid(row=2, column=0, sticky="ns", padx=(4, 0), pady=(0, 4))
        self._chapter_panel.grid_propagate(False)
        self._chapter_panel.grid_remove()

        tk.Label(self._chapter_panel, text="目 录", bg=XP_BG, font=("", 10, "bold")).pack(pady=(4, 2))

        list_frame = tk.Frame(self._chapter_panel, bg=XP_BG)
        list_frame.pack(fill="both", expand=True, padx=2, pady=(0, 4))

        self.chapter_listbox = tk.Listbox(
            list_frame, bg="white", fg="black",
            selectbackground=XP_BLUE, selectforeground="white",
            activestyle="none", bd=1, relief="sunken",
            exportselection=False,
            font=("Microsoft YaHei", 10))
        self.chapter_listbox.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(list_frame, orient="vertical",
                          command=self.chapter_listbox.yview)
        sb.pack(side="right", fill="y")
        self.chapter_listbox.configure(yscrollcommand=sb.set)
        self.chapter_listbox.bind("<<ListboxSelect>>", self._on_chapter_select)

    def _build_text_area(self):
        self._text_frame = tk.Frame(self, bg=XP_BG, bd=2, relief="sunken")
        self._text_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(4, 4), pady=(0, 4))

        self.text_area = tk.Text(
            self._text_frame, wrap="word", bg="white", fg="black",
            insertbackground="black",
            font=("Microsoft YaHei", self.config.get("font_size", 14)),
            padx=8, pady=6, bd=0,
            state="disabled")
        self.text_area.pack(side="left", fill="both", expand=True)

        text_sb = tk.Scrollbar(self._text_frame, orient="vertical",
                               command=self.text_area.yview)
        text_sb.pack(side="right", fill="y")
        self.text_area.configure(yscrollcommand=text_sb.set)

        self.text_area.tag_config("highlight", background="#FFFF99")
        self.text_area.tag_config("search_match", background="#FFB74D")
        size = self.config.get("font_size", 14)
        self.text_area.tag_config("title_tag",
                                  font=("Microsoft YaHei", size + 4, "bold"),
                                  foreground=XP_BLUE)

        self.text_area.bind("<Button-3>", self._on_text_right_click)
        self.text_area.bind("<ButtonRelease-1>", self._on_text_button_release)
        self.text_area.bind("<MouseWheel>", self._on_mousewheel)

    def _build_info_bar(self):
        self._info_bar = tk.Frame(self, bg=XP_BG, bd=1, relief="sunken")
        self._info_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=0, pady=0)
        self.status_label = tk.Label(self._info_bar, text="就绪", bg=XP_BG,
                                     fg="gray", anchor="w", font=("", 9),
                                     width=60, padx=6)
        self.status_label.pack(side="left", fill="x", expand=True, pady=1)

    def _set_icon(self):
        try:
            img = tk.PhotoImage(data=_load_icon_png())
            self.iconphoto(True, img)
            self._icon_img = img
        except Exception:
            pass

    def _show_welcome(self):
        self.text_area.configure(state="normal")
        self.text_area.delete("1.0", "end")
        self.text_area.insert("end", f"  《{APP_NAME}》 -阅他人，寻自己。\n ", "title_tag")
        self.text_area.insert("end", "  LevelUpReader v1.0\n")
        self.text_area.insert("end", "  中文TXT 有声小说阅读 · 多角色语音朗读\n\n")

        lines = [
            ("Ctrl+O",              "打开文件"),
            ("Ctrl+空格",           "播放 / 暂停"),
            ("Ctrl+\u2190",         "上一章"),
            ("Ctrl+\u2192",         "下一章"),
            ("Ctrl+Enter",          "从光标位置朗读"),
            ("E",                   "精简模式切换"),
            ("空格",                "老板键·隐藏窗口"),
            ("Ctrl+Alt",            "老板键·恢复窗口"),
            ("选中文字",             "朗读 / 复制"),
            ("滚轮",                 "滚动·章末自动翻章"),
            ("\u23ef \u23ee \u23ed \u23f9", "多媒体键也可控制播放"),
        ]
        max_key = max(len(k) for k, _ in lines)
        for key, desc in lines:
            pad = " " * (max_key - len(key) + 4)
            self.text_area.insert("end", f"  {key}{pad}{desc}\n")

        self.text_area.insert("end", "\n\n")
        self.text_area.insert("end", "  Powered by edge-tts · OpenCode AI\n")
        self.text_area.insert("end", "  github：https://github.com/zhege3/LevelUpReader\n")
        self.text_area.configure(state="disabled")

    def _bind_keys(self):
        self.bind("<Control-o>", lambda e: self._open_file())
        self.bind("<space>", lambda e: self._boss_key())
        self.bind("<Control-space>", lambda e: self._toggle_play())
        self.bind("<Control-Left>", lambda e: self._prev_chapter())
        self.bind("<Control-Right>", lambda e: self._next_chapter())
        self.bind("<Control-Return>", lambda e: self._play_from_cursor())
        self.bind("e", lambda e: self._toggle_minimal())
        self.bind("E", lambda e: self._toggle_minimal())
        self.bind("<KeyPress>", self._on_multimedia_key)

    # ---- Boss Key ----

    def _on_multimedia_key(self, event):
        kc = event.keycode
        if kc == 179:  # VK_MEDIA_PLAY_PAUSE
            self._toggle_play()
        elif kc == 177:  # VK_MEDIA_PREV_TRACK
            self._prev_chapter()
        elif kc == 176:  # VK_MEDIA_NEXT_TRACK
            self._next_chapter()
        elif kc == 178:  # VK_MEDIA_STOP
            self._stop()

    def _boss_key(self):
        if self.state() == 'withdrawn':
            return
        self.withdraw()
        self.after(200, self._poll_boss_restore)

    def _poll_boss_restore(self):
        if self.state() != 'withdrawn':
            return
        import ctypes
        ctrl = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        alt = ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000
        if ctrl and alt:
            self.after(400, self._boss_debounce)
            return
        self.after(100, self._poll_boss_restore)

    def _boss_debounce(self):
        import ctypes
        if self.state() != 'withdrawn':
            return
        ctrl = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        alt = ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000
        if ctrl and alt:
            self.after(200, self._boss_debounce)
        else:
            self.deiconify()
            self.focus_force()
            self._disable_ime()

    # ---- Minimal Mode ----

    def _toggle_minimal(self):
        if not hasattr(self, '_minimal_mode'):
            self._minimal_mode = False
        self._minimal_mode = not self._minimal_mode
        if self._minimal_mode:
            self._saved_title = self.title()
            self._saved_geo = self.geometry()
            self.title("")
            self.update_idletasks()
            rx = self._text_frame.winfo_rootx()
            ry = self._text_frame.winfo_rooty()
            rw = self._text_frame.winfo_width()
            rh = self._text_frame.winfo_height()
            self.grid_rowconfigure(2, weight=0)
            self._toolbar.grid_remove()
            self._info_bar.grid_remove()
            self._text_frame.grid_remove()
            if hasattr(self, '_search_bar') and self._search_bar.winfo_ismapped():
                self._search_bar.grid_remove()
            self.overrideredirect(True)
            self.minsize(1, 1)
            self.geometry(f"{rw}x{rh}+{rx}+{ry}")
            self._text_frame.place(x=0, y=0, relwidth=1, relheight=1)
            self._text_frame.bind("<Button-1>", self._minimal_drag_start)
            self._text_frame.bind("<B1-Motion>", self._minimal_drag_move)
            self.focus_force()
        else:
            self.overrideredirect(False)
            self.minsize(200, 120)
            self._text_frame.unbind("<Button-1>")
            self._text_frame.unbind("<B1-Motion>")
            self._text_frame.place_forget()
            self.grid_rowconfigure(2, weight=1)
            self._toolbar.grid()
            self._info_bar.grid()
            self._text_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(4, 4), pady=(0, 4))
            self.title(self._saved_title)
            self.geometry(self._saved_geo)
            self.focus_force()

    def _minimal_drag_start(self, event):
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    def _minimal_drag_move(self, event):
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self._drag_x = event.x_root
        self._drag_y = event.y_root
        x = self.winfo_x() + dx
        y = self.winfo_y() + dy
        self.geometry(f"+{x}+{y}")

    def _fix_taskbar(self):
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x40000
            WS_EX_TOOLWINDOW = 0x80
            hwnd = self.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = style | WS_EX_APPWINDOW
            style = style & ~WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    # ---- Chapter Panel Toggle ----

    def _toggle_chapter_panel(self):
        if self._chapter_visible:
            self._chapter_panel.grid_remove()
            self._text_frame.grid(column=0, columnspan=2)
            self._chapter_visible = False
        else:
            self._chapter_panel.grid()
            self._text_frame.grid(column=1, columnspan=1)
            self._chapter_visible = True

    # ---- Search ----

    def _toggle_search(self):
        if self._search_bar.winfo_ismapped():
            self._search_bar.grid_remove()
            self._clear_search_highlight()
        else:
            self._search_bar.grid()
            self._search_entry.focus_set()

    def _on_search_typing(self, event=None):
        self.after(100, self._do_search)

    def _do_search(self):
        term = self._search_entry.get()
        self._clear_search_highlight()
        self._search_matches = []
        self._search_index = -1
        if not term or self.current_chapter_idx < 0:
            self._search_count.configure(text="")
            return
        content = self.text_area.get("1.0", "end-1c")
        pos = "1.0"
        while True:
            pos = self.text_area.search(term, pos, stopindex="end", nocase=True)
            if not pos:
                break
            self._search_matches.append(pos)
            pos = f"{pos}+{len(term)}c"
        count = len(self._search_matches)
        self._search_count.configure(text=f"0/{count}" if count else "0")
        if self._search_matches:
            self._search_next()

    def _search_next(self):
        if not self._search_matches:
            self._do_search()
            return
        if not self._search_matches:
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        self._goto_match()

    def _search_prev(self):
        if not self._search_matches:
            self._do_search()
            return
        if not self._search_matches:
            return
        self._search_index = (self._search_index - 1) % len(self._search_matches)
        self._goto_match()

    def _goto_match(self):
        if not self._search_matches or self._search_index < 0:
            return
        pos = self._search_matches[self._search_index]
        term = self._search_entry.get()
        end_pos = f"{pos}+{len(term)}c"
        self._clear_search_highlight()
        self.text_area.configure(state="normal")
        self.text_area.tag_add("search_match", pos, end_pos)
        self.text_area.see(pos)
        self.text_area.configure(state="disabled")
        self._search_count.configure(
            text=f"{self._search_index + 1}/{len(self._search_matches)}")

    def _clear_search_highlight(self):
        try:
            self.text_area.tag_remove("search_match", "1.0", "end")
        except Exception:
            pass

    # ---- File Operations ----

    def _open_file(self):
        filepath = filedialog.askopenfilename(
            title="打开小说文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        try:
            content = load_file_with_encoding(filepath)
        except Exception as e:
            self._flash_status(f"无法打开文件: {e}")
            return
        self._stop()
        self.current_file = filepath
        self.chapters = split_chapters(content)
        filename = os.path.basename(filepath)
        self.file_label.configure(text=filename)
        self._rebuild_chapter_list()
        bookmark = BookmarkManager.load(filepath)
        start_chapter = 0
        scroll_pos = None
        if bookmark:
            bm_ch = bookmark.get("chapter_index", 0)
            if 0 <= bm_ch < len(self.chapters):
                start_chapter = bm_ch
                scroll_pos = bookmark.get("scroll_pos", 0)
        if self.chapters:
            self._load_chapter(start_chapter, scroll_pos)

    def _rebuild_chapter_list(self):
        self.chapter_listbox.delete(0, "end")
        self._chapter_labels = []
        for title, _ in self.chapters:
            short = (title[:16] + "...") if len(title) > 16 else title
            self.chapter_listbox.insert("end", short)
            self._chapter_labels.append(short)

    def _load_chapter(self, index, scroll_pos=None):
        if not self.chapters or index < 0 or index >= len(self.chapters):
            return
        self._stop()
        self.current_chapter_idx = index
        title, content = self.chapters[index]
        self.chapter_text = content
        self._compute_offsets(content)
        self.text_area.configure(state="normal")
        self.text_area.delete("1.0", "end")
        self.text_area.insert("end", title + "\n", "title_tag")
        self.text_area.insert("end", content)
        self.text_area.configure(state="disabled")
        self.chapter_info.configure(text=f"{title} ({index + 1}/{len(self.chapters)})")
        if scroll_pos is not None and scroll_pos > 0:
            self.text_area.yview_moveto(scroll_pos)
        else:
            self.text_area.see("1.0")
        self._highlight_chapter_item()
        self._save_bookmark()

    def _highlight_chapter_item(self):
        self.chapter_listbox.selection_clear(0, "end")
        if 0 <= self.current_chapter_idx < len(self._chapter_labels):
            self.chapter_listbox.selection_set(self.current_chapter_idx)
            self.chapter_listbox.see(self.current_chapter_idx)

    def _save_bookmark(self):
        if not self.current_file or self.current_chapter_idx < 0:
            return
        try:
            scroll = self.text_area.yview()[0]
        except Exception:
            scroll = 0
        BookmarkManager.save(self.current_file, self.current_chapter_idx, scroll)

    def _compute_offsets(self, text):
        self.sentence_offsets = []
        sentences = split_sentences(text)
        pos = 0
        for s in sentences:
            idx = text.find(s, pos)
            if idx >= 0:
                self.sentence_offsets.append(idx)
                pos = idx + len(s)
            else:
                self.sentence_offsets.append(pos)

    # ---- TTS Callbacks ----

    def _on_tts_sentence(self, index):
        self.after(0, self._highlight_sentence, index)

    def _highlight_sentence(self, index):
        try:
            self.text_area.configure(state="normal")
            self.text_area.tag_remove("highlight", "1.0", "end")
            if index < 0:
                self.text_area.configure(state="disabled")
                return

            if self._adv_segments and index < len(self._adv_segments):
                seg = self._adv_segments[index]
                seg_start, seg_end = seg[3], seg[4]
                title = self.chapters[self.current_chapter_idx][0]
                offset = len(title) + 1
                abs_start = offset + seg_start
                abs_end = offset + seg_end
                full_text = self.text_area.get("1.0", "end-1c")
                prefix = full_text[:abs_start]
                start_line = prefix.count('\n') + 1
                prev_nl = prefix.rfind('\n')
                start_col = abs_start - (prev_nl + 1) if prev_nl >= 0 else abs_start
                prefix2 = full_text[:abs_end]
                end_line = prefix2.count('\n') + 1
                prev_nl2 = prefix2.rfind('\n')
                end_col = abs_end - (prev_nl2 + 1) if prev_nl2 >= 0 else abs_end
                self.text_area.tag_add(
                    "highlight",
                    f"{start_line}.{start_col}",
                    f"{end_line}.{end_col}",
                )
                scroll_line = max(1, start_line - 4)
                self.text_area.see(f"{scroll_line}.0")
                self.text_area.configure(state="disabled")
                self._save_bookmark()
                return

            if index >= len(self.sentence_offsets):
                self.text_area.configure(state="disabled")
                return
            title = self.chapters[self.current_chapter_idx][0]
            offset = len(title) + 1
            char_pos = self.sentence_offsets[index]
            abs_start = offset + char_pos
            sentences = split_sentences(self.chapter_text)
            if index < len(sentences):
                abs_end = abs_start + len(sentences[index])
                full_text = self.text_area.get("1.0", "end-1c")
                prefix = full_text[:abs_start]
                start_line = prefix.count('\n') + 1
                prev_nl = prefix.rfind('\n')
                start_col = abs_start - (prev_nl + 1) if prev_nl >= 0 else abs_start
                prefix2 = full_text[:abs_end]
                end_line = prefix2.count('\n') + 1
                prev_nl2 = prefix2.rfind('\n')
                end_col = abs_end - (prev_nl2 + 1) if prev_nl2 >= 0 else abs_end
                self.text_area.tag_add(
                    "highlight",
                    f"{start_line}.{start_col}",
                    f"{end_line}.{end_col}",
                )
                scroll_line = max(1, start_line - 4)
                self.text_area.see(f"{scroll_line}.0")
            self.text_area.configure(state="disabled")
            self._save_bookmark()
        except Exception:
            try:
                self.text_area.configure(state="disabled")
            except Exception:
                pass

    def _on_tts_status(self, msg):
        self.after(0, self._update_play_ui, msg)

    def _update_play_ui(self, msg):
        self.status_label.configure(text=msg)
        if msg == "播放中" or msg.startswith("播放中"):
            self.btn_play.configure(text="暂停")
        elif msg == "已暂停":
            self.btn_play.configure(text="继续")
            self._save_bookmark()
        elif msg == "播放完成":
            self.btn_play.configure(text="播放")
            self._save_bookmark()
            self._auto_next_chapter()
        elif msg in ("已停止", "就绪"):
            self.btn_play.configure(text="播放")
        elif msg.startswith("生成语音"):
            self.btn_play.configure(text="暂停")

    def _auto_next_chapter(self):
        if self.current_chapter_idx < len(self.chapters) - 1:
            self._load_chapter(self.current_chapter_idx + 1)
            if self._adv_char_voices:
                self._start_advanced(self._adv_char_voices, self._adv_narration)
            else:
                self.tts.speak(self.chapter_text)
                self.btn_play.configure(text="暂停")
                self.status_label.configure(text="生成语音...")

    # ---- Playback Controls ----

    def _toggle_play(self):
        if self.current_chapter_idx < 0:
            self._flash_status("请先打开文件")
            return
        if not self.chapter_text:
            return
        if self.tts.is_playing:
            self.tts.pause()
            self.btn_play.configure(text="继续")
            self.status_label.configure(text="已暂停")
        elif self.tts.is_paused:
            self.tts.resume()
            self.btn_play.configure(text="暂停")
            self.status_label.configure(text="播放中")
        else:
            self._adv_segments = []
            self._adv_char_voices = {}
            self.btn_adv.configure(fg="black")
            self.tts.speak(self.chapter_text)
            self.btn_play.configure(text="暂停")
            self.status_label.configure(text="生成语音...")

    # ---- Advanced Playback ----

    def _toggle_advanced(self):
        if self.current_chapter_idx < 0:
            self._flash_status("请先打开文件")
            return
        if not self.current_file or not self.chapter_text:
            return
        self._flash_status("分析角色中...")
        characters = CharacterAnalyzer.analyze(self.chapter_text)
        if not characters:
            self._flash_status("未检测到角色对话")
            return
        narration = "zh-CN-XiaoxiaoNeural"
        char_voices = {}
        mi = 0
        fi = 0
        for i, char in enumerate(characters):
            if i % 2 == 0:
                voice = MALE_VOICES[mi % len(MALE_VOICES)]
                mi += 1
            else:
                voice = FEMALE_VOICES[fi % len(FEMALE_VOICES)]
                fi += 1
                while voice == narration and fi < len(FEMALE_VOICES) * 2:
                    voice = FEMALE_VOICES[fi % len(FEMALE_VOICES)]
                    fi += 1
            char_voices[char] = voice
        BookmarkManager.save_characters(self.current_file, char_voices, narration)
        self._start_advanced(char_voices, narration)

    def _start_advanced(self, char_voices, narration_voice):
        segments = ScriptParser.parse(
            self.chapter_text, char_voices, narration_voice)
        if not segments:
            self._flash_status("解析失败")
            return
        self._adv_segments = segments
        self._adv_char_voices = char_voices
        self._adv_narration = narration_voice
        self.tts.speak_segments(segments)
        self.btn_play.configure(text="暂停")
        self.btn_adv.configure(fg="blue")
        self.status_label.configure(text="高级播放 生成语音...")

    def _stop(self):
        self.tts.stop()
        self._adv_segments = []
        self.btn_play.configure(text="播放")
        self.btn_adv.configure(fg="black")
        self.status_label.configure(text="已停止")
        try:
            self.text_area.configure(state="normal")
            self.text_area.tag_remove("highlight", "1.0", "end")
            self.text_area.configure(state="disabled")
        except Exception:
            pass
        self._save_bookmark()

    def _prev_chapter(self):
        if self.current_chapter_idx > 0:
            self._load_chapter(self.current_chapter_idx - 1)

    def _next_chapter(self):
        if self.current_chapter_idx < len(self.chapters) - 1:
            self._load_chapter(self.current_chapter_idx + 1)

    def _on_mousewheel(self, event):
        yv = self.text_area.yview()
        direction = -1 if event.delta > 0 else 1

        # At bottom, scroll down → next chapter
        if direction > 0 and yv[1] >= 1.0:
            if self.current_chapter_idx < len(self.chapters) - 1:
                self._load_chapter(self.current_chapter_idx + 1)
            return "break"

        # At top, scroll up → previous chapter (show end)
        if direction < 0 and yv[0] <= 0.0:
            if self.current_chapter_idx > 0:
                self._load_chapter(self.current_chapter_idx - 1)
                self.text_area.see("end")
            return "break"

        self.text_area.yview_scroll(direction, "units")
        return "break"

    def _on_chapter_select(self, event=None):
        sel = self.chapter_listbox.curselection()
        if sel:
            self._load_chapter(sel[0])

    # ---- Text interactions ----

    def _on_text_button_release(self, event):
        self.after(80, self._check_selection, event)

    def _check_selection(self, event):
        try:
            sel = self.text_area.get("sel.first", "sel.last")
            if sel.strip():
                self._show_selection_bar(event)
                return
        except tk.TclError:
            pass
        self._hide_selection_bar()

    def _show_selection_bar(self, event):
        x = event.x_root - self.winfo_rootx()
        y = event.y_root - self.winfo_rooty() - 32
        if not hasattr(self, '_sel_bar'):
            self._sel_bar = tk.Frame(self, bg=XP_BG, bd=1, relief="raised")
            tk.Button(self._sel_bar, text="朗读", relief="raised", bd=2,
                      command=self._on_sel_bar_play).pack(side="left", padx=1, pady=1)
            tk.Button(self._sel_bar, text="复制", relief="raised", bd=2,
                      command=self._on_sel_bar_copy).pack(side="left", padx=1, pady=1)
        self._sel_bar.place(x=x, y=y)
        self._sel_bar.lift()

    def _hide_selection_bar(self):
        if hasattr(self, '_sel_bar'):
            self._sel_bar.place_forget()

    def _on_sel_bar_play(self):
        self._hide_selection_bar()
        self._play_from_cursor()

    def _on_sel_bar_copy(self):
        self._copy_selection()
        self._hide_selection_bar()

    def _play_from_cursor(self):
        if self.current_chapter_idx < 0 or not self.chapter_text:
            self._flash_status("请先打开文件")
            return
        cursor_pos = self.text_area.index("insert")
        full_text = self.text_area.get("1.0", cursor_pos)
        char_pos_in_widget = len(full_text)
        title = self.chapters[self.current_chapter_idx][0]
        title_offset = len(title) + 1
        char_pos_in_chapter = max(0, char_pos_in_widget - title_offset)
        sentence_idx = 0
        for i, offset in enumerate(self.sentence_offsets):
            if char_pos_in_chapter >= offset:
                sentence_idx = i
            else:
                break
        self.tts.speak(self.chapter_text, start_index=sentence_idx)
        self.btn_play.configure(text="暂停")
        self.status_label.configure(text="生成语音...")

    def _copy_selection(self):
        try:
            sel = self.text_area.get("sel.first", "sel.last")
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except tk.TclError:
            pass

    def _on_text_right_click(self, event):
        self.text_area.mark_set("insert", f"@{event.x},{event.y}")
        self.text_area.focus_set()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="从此处朗读", command=self._play_from_cursor)
        menu.add_separator()
        menu.add_command(label="复制", command=self._copy_selection)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---- Settings handlers ----

    def _on_voice_change(self, event=None):
        choice = self.voice_var.get()
        for vid, vname in CHINESE_VOICES:
            if vname == choice:
                self.tts.voice = vid
                self.config.set("voice", vid)
                break

    def _on_speed_change(self, value):
        spd = round(float(value), 1)
        self.tts.speed = spd
        self.speed_label.configure(text=f"{spd:.1f}x")
        self.config.set("speed", spd)

    def _font_larger(self):
        size = self.config.get("font_size", 14)
        size = min(32, size + 2)
        self.config.set("font_size", size)
        self.text_area.configure(font=("Microsoft YaHei", size))
        self.text_area.tag_config("title_tag",
                                  font=("Microsoft YaHei", size + 4, "bold"),
                                  foreground=XP_BLUE)

    def _font_smaller(self):
        size = self.config.get("font_size", 14)
        size = max(10, size - 2)
        self.config.set("font_size", size)
        self.text_area.configure(font=("Microsoft YaHei", size))
        self.text_area.tag_config("title_tag",
                                  font=("Microsoft YaHei", size + 4, "bold"),
                                  foreground=XP_BLUE)

    def _flash_status(self, msg):
        self.status_label.configure(text=msg, fg="red")
        self.after(3000, lambda: self.status_label.configure(fg="gray"))

    # ---- Lifecycle ----

    def _on_close(self):
        self._save_bookmark()
        try:
            self.config.set("window_geometry", self.geometry())
        except Exception:
            pass
        self._stop()
        self.tts.cleanup()
        self.destroy()

    def run(self):
        self.mainloop()


if __name__ == "__main__":
    app = NovelReaderApp()
    app.run()
