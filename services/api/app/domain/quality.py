"""Deterministic 28-dimension music-text evaluator.

The frozen v2.1-Final TEE mathematics are implemented verbatim. Text-to-feature
extraction is intentionally a transparent, replayable reference heuristic; the
production extension point is a versioned language analyzer/lexicon, never an
unversioned LLM score.
"""
from __future__ import annotations

import math
import re
from typing import Any

ENGINE_VERSION = "v12-reference-28d-2.1-final"
RULESET_VERSION = "music-text-v2.1-final-reference-2026-08"

STYLE_WEIGHTS = [15, 15, 10, 10, 8, 5, 5, 5, 10, 10, 5, 3, 2, 2]
LYRIC_MAX_CANTONESE = [30, 35, 35, 25, 25, 45, 55, 40, 35, 25, 25, 25, 35, 35]
LYRIC_MAX_MANDARIN = [30, 35, 35, 25, 20, 45, 55, 40, 35, 25, 25, 25, 35, 35]

PRIORITY = {
    **{f"V{i}": "Core" for i in (1, 2, 9, 10, 11, 15, 16, 17, 20, 21, 22, 27)},
    **{f"V{i}": "Important" for i in (3, 4, 5, 8, 18, 19, 23, 24, 26, 28)},
    **{f"V{i}": "Auxiliary" for i in (6, 7, 12, 13, 14, 25)},
}

IMAGE_WORDS = [
    "雨", "灯", "门", "玻璃", "影子", "海", "风", "夜", "城市", "车站", "站台", "桌面", "月",
    "椅子", "打卡机", "微波炉", "按钮", "纸盒", "标签", "倒影", "贩卖机", "衣服", "钥匙", "杯",
    "snow", "rain", "window", "street", "shadow", "ocean", "chair", "key", "glass", "screen",
]
ACTION_WORDS = [
    "推", "拉", "撕", "攥", "渗", "按", "扯", "搁", "关", "揉", "钉", "刻", "埋", "吞", "嵌", "掷", "戳",
    "握", "走", "看见", "听见", "落", "裂", "燃", "褪", "戴", "除", "挂", "贴", "掉", "长进",
    "㩒", "搣", "揿", "捽", "run", "fall", "break", "hold", "walk", "burn", "tear", "bury",
]
COMMON_AI_IMAGES = ["月光", "雨", "风", "泪水", "黑夜", "星辰", "玫瑰", "翅膀", "火焰"]
COMMON_AI_VERBS = ["想", "看", "听", "说", "觉得", "希望", "等待", "相信", "记得", "忘记"]

MANDARIN_TONES = {
    "锁": 3, "敢": 3, "我": 3, "枪": 1, "灯": 1, "声": 1, "时": 2, "形": 2, "霓": 2,
    "事": 4, "路": 4, "去": 4, "亮": 4, "爱": 4, "夜": 4, "开": 1, "来": 2, "风": 1,
    "光": 1, "梦": 4, "手": 3, "海": 3, "走": 3, "口": 3, "了": 0, "的": 0, "吧": 0,
}
CANTONESE_TONES = {
    "锁": 2, "敢": 2, "枪": 1, "灯": 1, "声": 1, "时": 4, "形": 4, "霓": 4, "播": 3,
    "跳": 3, "掣": 3, "事": 6, "话": 6, "路": 6, "市": 5, "有": 5, "我": 5,
    "得": 7, "答": 8, "十": 9,
}


def _text(spec: dict[str, Any], key: str, default: Any = "") -> str:
    value = spec.get(key, default)
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value or "")


def _count_any(text: str, words: list[str]) -> int:
    low = text.lower()
    return sum(1 for word in words if word.lower() in low)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _dim(code: str, name: str, raw: float, max_points: int | None = None, note: str = "") -> dict[str, Any]:
    raw = _clamp(float(raw), 0.0, 5.0)
    result: dict[str, Any] = {
        "code": code,
        "name": name,
        "priority": PRIORITY[code],
        "raw": round(raw, 2),
        "max": 5,
        "pct": round(raw / 5 * 100, 1),
        "note": note,
    }
    if max_points is not None:
        result["points"] = round(raw / 5 * max_points, 2)
        result["max_points"] = max_points
    return result


def _is_cantonese(spec: dict[str, Any], lyrics: str) -> bool:
    language = _text(spec, "language").lower()
    return language in {"yue", "yue-hant", "zh-hk", "cantonese", "粤语", "粵語"} or _count_any(lyrics, ["唔", "冇", "嘅", "咁", "佢", "喺"]) >= 2


def _is_fast(spec: dict[str, Any], styles: str) -> bool:
    genre = (_text(spec, "genre") + " " + styles).lower()
    bpm = float(spec.get("bpm") or 0)
    return bpm >= 96 or any(word in genre for word in ("funk", "synth-pop", "synthpop", "rock", "dance", "舞曲", "摇滚", "搖滾"))


def _extract_sections(lyrics: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "untagged"
    for raw_line in lyrics.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\[([^\]]+)\]|^【([^】]+)】", line)
        if match:
            tag = (match.group(1) or match.group(2) or "").lower()
            if any(x in tag for x in ("final chorus", "chorus 3", "副歌3", "副歌 3", "最后副歌", "最終副歌")):
                current = "final_chorus"
            elif "chorus" in tag or "副歌" in tag:
                current = "chorus"
            elif "bridge" in tag or "桥" in tag or "橋" in tag:
                current = "bridge"
            elif "outro" in tag or "尾奏" in tag:
                current = "outro"
            elif "verse" in tag or "主歌" in tag:
                current = "verse"
            else:
                current = tag
            sections.setdefault(current, [])
        elif line:
            sections.setdefault(current, []).append(line)
    return sections


def _pac_raw(lyrics: str, sections: dict[str, list[str]]) -> tuple[float, dict[str, Any]]:
    low = lyrics.lower()
    image_count = _count_any(lyrics, IMAGE_WORDS)
    if image_count == 0:
        return 0.0, {"nodes": [], "parasite_form": "none", "coefficient": 0.0}

    nodes: list[tuple[str, float]] = [("initial_state", 0.5)]
    contact_terms = ["手", "胸", "脖", "颈", "頸", "肩", "脸", "臉", "肉", "皮肤", "皮膚", "身体", "身體", "掌心", "挂在", "掛在", "贴在", "貼在", "抱", "握", "戴"]
    if _count_any(lyrics, contact_terms) and _count_any(lyrics, ACTION_WORDS):
        nodes.append(("human_contact", 1.0))
    transition_terms = ["裂开", "裂開", "掉落", "落下", "变轻", "變輕", "变重", "變重", "褪色", "磨损", "磨損", "碎", "熄灭", "熄滅", "长进", "長進", "撕", "丢掉", "丟掉"]
    if _count_any(lyrics, transition_terms):
        nodes.append(("turning_point", 1.5))
    ending = "\n".join(sections.get("outro", []) + sections.get("final_chorus", [])[-2:])
    if ending and _count_any(ending, IMAGE_WORDS):
        nodes.append(("final_landing", 2.0))

    tactile_terms = ["冷", "热", "熱", "刺", "软", "軟", "硬", "黏", "烫", "燙", "温", "溫", "触", "觸"]
    spatial_terms = ["挂在", "掛在", "贴在", "貼在", "脖", "胸", "肩", "掌心", "皮肤", "皮膚", "肉里", "肉裡"]
    time_terms = ["记得", "記得", "那年", "曾经", "曾經", "旧", "舊", "褪色", "磨损", "磨損", "后来", "後來"]
    candidates = []
    if _count_any(lyrics, tactile_terms): candidates.append(("tactile", 1.5))
    if _count_any(lyrics, spatial_terms): candidates.append(("spatial", 1.2))
    if _count_any(lyrics, time_terms): candidates.append(("temporal", 1.8))
    parasite_form, coefficient = max(candidates, key=lambda item: item[1], default=("none", 0.0))
    raw = min(9.0, sum(weight for _, weight in nodes) * coefficient)
    return raw, {"nodes": [name for name, _ in nodes], "parasite_form": parasite_form, "coefficient": coefficient, "image_anchor_count": image_count, "source_text_present": bool(low)}


def _style_bucket(spec: dict[str, Any], styles: str) -> str:
    text = (_text(spec, "genre") + " " + styles).lower()
    if "opera" in text or "orchestral" in text or "歌剧" in text or "歌劇" in text: return "opera"
    if "folk" in text or "民谣" in text or "民謠" in text: return "folk"
    if "rock" in text or "band sound" in text or "摇滚" in text or "搖滾" in text: return "rock"
    if "synth" in text or "dance" in text or "舞曲" in text: return "synth"
    if "funk" in text: return "funk"
    return "slow"


def _tsmi_raw(spec: dict[str, Any], hook: str, styles: str, is_cantonese: bool) -> tuple[float, dict[str, Any]]:
    if not hook.strip():
        return 0.0, {"confidence": "none", "reason": "hook missing"}
    last = next((char for char in reversed(hook.strip()) if not char.isspace() and char not in "，。！？,.!?"), "")
    bucket = _style_bucket(spec, styles)
    if is_cantonese:
        tone = CANTONESE_TONES.get(last)
        base = {1: 9, 2: 10, 3: 7, 4: 8, 5: 5, 6: 6, 7: 4, 8: 3, 9: 2}.get(tone or -1)
        coefficients = {
            "slow": {2: 1.5, 1: 1.3, 7: -1.0, 8: -1.0, 9: -1.0},
            "funk": {7: 1.5, 8: 1.5, 9: 1.5, 3: 1.3, 4: 0.5},
            "synth": {3: 1.5, 7: 1.3, 8: 1.3, 9: 1.3, 4: 0.6},
            "folk": {4: 1.3, 1: 1.2, 7: 0.8, 8: 0.8, 9: 0.8},
            "rock": {3: 1.5, 1: 1.2, 5: 0.5},
            "opera": {1: 1.5, 4: 1.3, 7: 0.7, 8: 0.7, 9: 0.7},
        }
    else:
        tone = MANDARIN_TONES.get(last)
        base = {3: 10, 1: 9, 2: 8, 4: 6, 0: 2}.get(tone if tone is not None else -1)
        coefficients = {
            "slow": {3: 1.5, 1: 1.3, 4: 0.3},
            "funk": {4: 1.5, 1: 1.2, 3: 0.5},
            "synth": {4: 1.5, 1: 1.3, 3: 0.4},
            "folk": {3: 1.5, 1: 1.3, 4: 0.5},
            "rock": {4: 1.5, 1: 1.2, 3: 0.4},
            "opera": {1: 1.5, 2: 1.3, 4: 0.6},
        }
    if base is None or tone is None:
        return 0.0, {"confidence": "low", "hook_final": last, "tone": None, "style_bucket": bucket, "reason": "tone lexicon miss; neutral fallback"}
    coefficient = coefficients[bucket].get(tone, 1.0)
    raw = _clamp(float(base) * coefficient * 2.0, -30.0, 30.0)
    return raw, {"confidence": "reference_lexicon", "hook_final": last, "tone": tone, "base": base, "coefficient": coefficient, "position_weight": 2.0, "style_bucket": bucket}


def _nsrq_raw(lyrics: str, fast: bool) -> tuple[float, dict[str, Any]]:
    durations = [float(value) for value in re.findall(r"\[pause\s*([0-9]+(?:\.[0-9]+)?)s\]", lyrics, re.I)]
    silences = [float(value) for value in re.findall(r"\[silence\s*([0-9]+(?:\.[0-9]+)?)s\]", lyrics, re.I)]
    abrupt = bool(re.search(r"\[Abrupt Stop\]", lyrics, re.I))
    if fast:
        ideals = [(0.3, 0.5), (0.8, 1.0), (1.5, 2.0), (1.0, 0.8)]
        used = durations[:4]
        raw = sum(_clamp(actual / ideal) * weight for actual, (ideal, weight) in zip(used, ideals)) + (1.0 if abrupt else 0.0)
        ceiling = 5.3
    else:
        ideals = [(0.5, 0.5), (2.0, 1.5), (3.0, 2.0), (1.5, 0.8)]
        used = durations[:4]
        raw = sum(_clamp(actual / ideal) * weight for actual, (ideal, weight) in zip(used, ideals))
        if silences:
            raw += _clamp(max(silences) / 5.0) * 1.0
        ceiling = 5.8
    return min(ceiling, raw), {"mode": "fast" if fast else "slow", "pause_durations": durations, "silence_durations": silences, "abrupt_stop": abrupt, "theoretical_max": ceiling}


def _c3ac_raw(lyrics: str, hook: str, sections: dict[str, list[str]]) -> tuple[float, dict[str, Any]]:
    final_lines = sections.get("final_chorus") or sections.get("chorus") or []
    final_text = "\n".join(final_lines)
    first_two = "\n".join(final_lines[:2])
    last_two = "\n".join(final_lines[-2:])
    insight = ["我知", "原来", "原來", "其实", "其實", "不过", "不過", "只系", "終於", "终于", "到头来", "到頭來", "后来才懂", "後來才懂", "我终于知道", "我終於知道"]
    claiming = ["留低", "唔抆", "等", "替", "当系", "當係", "由佢", "揸住", "揽实", "攬實", "继续", "繼續", "仍然", "由它", "留着", "留著", "就当是", "讓它", "让它", "放着", "放著", "等着", "等著", "还是", "還是", "依然", "放开", "放開", "算了", "走吧", "不再", "忘了", "丢掉", "丟掉", "已经够了", "已經夠了", "自由", "重生"]
    d = 1.0 if _count_any(first_two, insight) else 0.0
    has_claim = bool(_count_any(last_two, claiming))
    has_image = bool(_count_any(last_two, IMAGE_WORDS) or (hook and hook in last_two))
    c = 1.0 if has_claim and has_image else 0.5 if has_claim else 0.0
    prior_chorus = "\n".join(sections.get("chorus", []))
    skeleton = bool(hook and hook in final_text and (hook in prior_chorus or lyrics.count(hook) >= 2))
    transition = bool(d and has_claim)
    l = 1.0 if skeleton and transition else 0.5 if skeleton or transition else 0.0
    raw = 0.6 * d + 0.7 * c + 0.7 * l
    return raw, {"D": d, "C": c, "L": l, "final_chorus_detected": bool(final_lines), "hook_skeleton_preserved": skeleton}


def _tee(language_is_cantonese: bool, lyrics_pct: float, pac_raw: float, tsmi_raw: float, nsrq_raw: float, c3ac_raw: float) -> dict[str, float]:
    pac_n = _clamp(pac_raw / 9.0)
    tsmi_n = _clamp(tsmi_raw / 30.0, -1.0, 1.0)
    nsrq_n = _clamp(nsrq_raw / 5.0)
    c3ac_n = _clamp(c3ac_raw / 2.0)
    base = 340.0 if language_is_cantonese else 335.0
    full_score = 470.0 if language_is_cantonese else 465.0
    tee_raw = base + 32*pac_n + 24*tsmi_n + 20*nsrq_n + 28*c3ac_n + 16*(pac_n*tsmi_n) + 12*(nsrq_n*c3ac_n)
    tee_pct_raw = _clamp(tee_raw / full_score, 0.0, 1.0) * 100

    gates = {
        "gPAC": _clamp(0.85*pac_n + 0.15),
        "gTSMI": _clamp(0.85*tsmi_n + 0.15),
        "gNSRQ": _clamp(0.85*nsrq_n + 0.15),
        "gC3AC": _clamp(0.85*c3ac_n + 0.15),
    }
    if min(gates.values()) <= 0:
        g_struct = 0.0
    else:
        eps = 1e-8
        g_struct = math.exp(
            0.30*math.log(max(gates["gPAC"], eps))
            + 0.25*math.log(max(gates["gTSMI"], eps))
            + 0.20*math.log(max(gates["gNSRQ"], eps))
            + 0.25*math.log(max(gates["gC3AC"], eps))
        )
    soft_penalty = 35.0 * (1.0 - g_struct)
    critical_tsmi = 20.0 * _clamp(-tsmi_n / 0.15)
    critical_pac = 10.0 * _clamp((0.20 - pac_n) / 0.20)
    critical_c3ac = 10.0 * _clamp((0.25 - c3ac_n) / 0.25)
    critical_penalty = critical_tsmi + critical_pac + critical_c3ac
    tee_pct_final = _clamp((tee_pct_raw - soft_penalty - critical_penalty) / 100.0) * 100
    return {
        "PAC_raw": pac_raw, "TSMI_raw": tsmi_raw, "NSRQ_raw": nsrq_raw, "C3AC_raw": c3ac_raw,
        "PAC_n": pac_n, "TSMI_n": tsmi_n, "NSRQ_n": nsrq_n, "C3AC_n": c3ac_n,
        "TEE_base": base, "LyricsFullScore": full_score, "TEE_raw": tee_raw, "TEE_pct_raw": tee_pct_raw,
        **gates, "G_struct": g_struct, "SoftPenalty": soft_penalty,
        "CriticalPenalty": critical_penalty, "CriticalPenalty_TSMI": critical_tsmi,
        "CriticalPenalty_PAC": critical_pac, "CriticalPenalty_C3AC": critical_c3ac,
        "TEE_pct_final": tee_pct_final, "TEE_offset": tee_pct_final - lyrics_pct,
    }


def evaluate(spec: dict[str, Any]) -> dict[str, Any]:
    styles = _text(spec, "styles")
    lyrics = _text(spec, "lyrics")
    title = _text(spec, "title")
    theme = _text(spec, "theme")
    mood = _text(spec, "mood")
    genre = _text(spec, "genre")
    vocal = spec.get("vocal") or {}
    structure = spec.get("structure") or {}
    hook = _text(spec, "hook")
    combined = " ".join([styles, genre, mood, str(vocal), str(structure)])
    sections = _extract_sections(lyrics)
    is_cantonese = _is_cantonese(spec, lyrics)
    fast = _is_fast(spec, styles)

    pac_raw, pac_evidence = _pac_raw(lyrics, sections)
    tsmi_raw, tsmi_evidence = _tsmi_raw(spec, hook, styles, is_cantonese)
    nsrq_raw, nsrq_evidence = _nsrq_raw(lyrics, fast)
    c3ac_raw, c3ac_evidence = _c3ac_raw(lyrics, hook, sections)

    style_dims: list[dict[str, Any]] = []
    style_dims.append(_dim("V1", "人声描述", 1 + min(4, _count_any(combined, ["vocal", "人声", "人聲", "breathy", "rasp", "baritone", "soprano", "intimate", "咬字", "气声", "氣聲", "vibrato", "resonance"])), note="无人声标签时一票否决"))
    style_dims.append(_dim("V2", "风格语义", 1 + min(4, len([x for x in re.split(r"[,，/]", styles) if x.strip()]) / 3), note="标签数量仅是语义覆盖的可重放代理"))
    style_dims.append(_dim("V3", "配器层次", min(5, _count_any(combined, ["piano", "guitar", "drum", "bass", "synth", "strings", "pad", "rhodes", "钢琴", "鋼琴", "吉他", "鼓", "贝斯", "貝斯", "弦乐", "弦樂"]))))
    style_dims.append(_dim("V4", "混音明确性", min(5, _count_any(combined, ["mix", "compression", "warmth", "stereo", "lufs", "saturation", "混音", "压缩", "壓縮", "立体声", "立體聲", "db"]))))
    style_dims.append(_dim("V5", "空间混响", min(5, _count_any(combined, ["reverb", "delay", "room", "plate", "space", "混响", "混響", "延迟", "延遲", "空间", "空間", "ms"]))))
    style_dims.append(_dim("V6", "低频定义", min(5, _count_any(combined, ["sub", "bass", "low-end", "低频", "低頻", "贝斯", "貝斯"]))))
    style_dims.append(_dim("V7", "高频定义", min(5, _count_any(combined, ["air", "sheen", "bright", "cymbal", "high-end", "高频", "高頻", "明亮", "harsh"]))))
    style_dims.append(_dim("V8", "动态设计", min(5, _count_any(combined, ["crescendo", "dynamic", "build", "drop", "克制", "递进", "遞進", "爆发", "爆發", "dynamic", "held back", "full energy"]))))
    structure_terms = ["verse", "chorus", "bridge", "outro", "intro", "主歌", "副歌", "桥段", "橋段", "尾奏"]
    style_dims.append(_dim("V9", "结构标签", min(5, _count_any(styles + " " + str(structure) + " " + lyrics, structure_terms))))
    style_dims.append(_dim("V10", "Hook 设计", min(5, (2 if hook else 0) + min(3, lyrics.count(hook) if hook else 0))))
    emotion_terms = ["restrained", "detached", "warm", "cold", "intimate", "tense", "克制", "冷感", "温暖", "溫暖", "贴耳", "貼耳"]
    style_dims.append(_dim("V11", "情绪一致性", min(5, 1 + _count_any(styles, emotion_terms) + (1 if mood else 0))))
    style_dims.append(_dim("V12", "人声前置", min(5, _count_any(combined, ["front", "forward", "close", "intimate", "贴耳", "貼耳", "靠前", "清晰人声", "vocal +", "db above"]))))
    style_dims.append(_dim("V13", "排除项", min(5, _count_any(styles, ["no ", "without", "exclude", "不要", "禁止"]))))
    pre_v14_score = sum(d["raw"] / 5 * weight for d, weight in zip(style_dims, STYLE_WEIGHTS[:-1])) / sum(STYLE_WEIGHTS[:-1]) * 100
    core_ok = all(d["raw"] >= 3 for d in style_dims if d["priority"] == "Core")
    v14 = 5 if pre_v14_score >= 85 and core_ok else 4 if pre_v14_score >= 70 and core_ok else 3 if pre_v14_score >= 55 and all(d["raw"] > 1 for d in style_dims) else 2 if pre_v14_score >= 40 else 1
    style_dims.append(_dim("V14", "整体完成度", v14, note=f"基于 V1–V13 加权分 {pre_v14_score:.1f}"))

    lines = [x.strip() for x in lyrics.splitlines() if x.strip() and not x.strip().startswith("[") and not x.strip().startswith("【")]
    narrative_terms = ["那天", "后来", "後來", "当时", "當時", "门口", "門口", "站台", "凌晨", "去年", "今天", "昨天", "today", "yesterday", "midnight"]
    colloquial = ["我", "你", "别", "別", "其实", "其實", "原来", "原來", "还是", "還是", "就", "唔", "冇", "嘅", "咁", "I ", "you "]
    performance = ["breath", "whisper", "falsetto", "rasp", "气声", "氣聲", "耳语", "耳語", "真假声", "真假聲", "破音", "颤音", "顫音", "naked voice", "cracks"]
    dynamic_terms = ["very soft", "building", "released", "heavier", "stronger", "naked voice", "full voice", "cracks open", "low energy", "detached", "full energy", "beyond full", "silence", "abrupt stop", "极轻", "極輕", "递进", "遞進", "骤停", "驟停", "全开", "全開", "破音"]
    generic_image_hits = _count_any(lyrics, COMMON_AI_IMAGES)
    generic_verb_hits = _count_any(lyrics, COMMON_AI_VERBS)

    lyric_maxima = LYRIC_MAX_CANTONESE if is_cantonese else LYRIC_MAX_MANDARIN
    lyric_dims: list[dict[str, Any]] = []
    lyric_dims.append(_dim("V15", "主题选择", 5 if theme and (_count_any(theme + lyrics, IMAGE_WORDS) or len(theme) >= 4) else 3 if title else 1, lyric_maxima[0]))
    v16 = 1 + min(2.5, _count_any(lyrics + theme, IMAGE_WORDS) / 2) + (1.5 if pac_raw >= 5 else 1 if pac_raw >= 3 else 0 if pac_raw >= 1 else -0.5)
    lyric_dims.append(_dim("V16", "物化意象", v16, lyric_maxima[1], note=f"PAC_raw={pac_raw:.2f}"))
    detail_count = len(re.findall(r"\d{1,2}[:点時时]|" + "|".join(map(re.escape, narrative_terms)), lyrics, re.I))
    lyric_dims.append(_dim("V17", "叙事细节", min(5, 1 + detail_count + len(lines)/14), lyric_maxima[2]))
    precise_verbs = _count_any(lyrics, ACTION_WORDS)
    lyric_dims.append(_dim("V18", "动词精准", min(5, 1 + precise_verbs/2 - min(1, generic_verb_hits/5)), lyric_maxima[3]))
    lyric_dims.append(_dim("V19", "口语底色", min(5, 1 + _count_any(lyrics, colloquial)/3), lyric_maxima[4]))
    hook_len = len(re.sub(r"\s+", "", hook))
    hook_repeats = lyrics.count(hook) if hook else 0
    v20 = (1 if hook else 0) + (1.5 if hook and hook_len <= 4 else 0.7 if hook_len <= 8 else 0) + min(2, hook_repeats/2) + (0.5 if tsmi_raw >= 15 else 0 if tsmi_raw >= 8 else -0.5 if tsmi_raw >= 0 else -1.5)
    lyric_dims.append(_dim("V20", "Hook 设计", v20, lyric_maxima[5], note=f"TSMI_raw={tsmi_raw:.2f}"))
    section_count = sum(bool(sections.get(name)) for name in ("verse", "chorus", "bridge", "final_chorus", "outro"))
    v21 = 1 + min(2.5, section_count/2) + (1.5 if c3ac_raw >= 1.5 else 0.8 if c3ac_raw >= 1 else 0.3 if c3ac_raw >= 0.5 else -0.5)
    lyric_dims.append(_dim("V21", "结构建筑", v21, lyric_maxima[6], note=f"C3AC_raw={c3ac_raw:.2f}"))
    bridge_text = "\n".join(sections.get("bridge", []))
    v22 = 1 + (1.5 if bridge_text else 0) + min(1.5, _count_any(lyrics, ["all instruments cut", "naked voice", "breakdown", "骤停", "驟停", "裸声", "裸聲"])) + (1 if nsrq_raw >= 4 else 0.5 if nsrq_raw >= 2.5 else 0)
    lyric_dims.append(_dim("V22", "Bridge 设计", v22, lyric_maxima[7], note=f"NSRQ_raw={nsrq_raw:.2f}"))
    outro_text = "\n".join(sections.get("outro", []))
    lyric_dims.append(_dim("V23", "尾奏处理", min(5, 1 + (1.5 if outro_text else 0) + (1.5 if re.search(r"\[silence|\[Abrupt Stop", lyrics, re.I) else 0) + (1 if _count_any(outro_text, IMAGE_WORDS + ACTION_WORDS) else 0)), lyric_maxima[8]))
    lyric_dims.append(_dim("V24", "演唱质感", min(5, 1 + _count_any(lyrics + styles, performance)), lyric_maxima[9]))
    lyric_dims.append(_dim("V25", "编曲配置", min(5, 1 + _count_any(lyrics + styles, ["piano", "guitar", "drum", "bass", "synth", "strings", "钢琴", "鋼琴", "吉他", "鼓", "弦乐", "弦樂"])/2), lyric_maxima[10]))
    pauses_count = len(nsrq_evidence["pause_durations"]) + len(nsrq_evidence["silence_durations"]) + int(nsrq_evidence["abrupt_stop"])
    lyric_dims.append(_dim("V26", "留白负空间", min(5, 1 + pauses_count), lyric_maxima[11], note=f"NSRQ mode={nsrq_evidence['mode']}"))
    v27 = 1 if tsmi_evidence["confidence"] == "none" else 3 if tsmi_evidence["confidence"] == "low" else _clamp(3 + tsmi_raw/15, 1, 5)
    lyric_dims.append(_dim("V27", "声调协音", v27, lyric_maxima[12], note=f"{tsmi_evidence['confidence']}; hook final={tsmi_evidence.get('hook_final','')}"))
    dynamic_coverage = _count_any(lyrics + styles, dynamic_terms)
    lyric_dims.append(_dim("V28", "动态弧线", min(5, 1 + dynamic_coverage/2 + (1 if c3ac_raw >= 1.5 else 0)), lyric_maxima[13]))

    style_score = sum(d["raw"] / 5 * weight for d, weight in zip(style_dims, STYLE_WEIGHTS)) / sum(STYLE_WEIGHTS) * 100
    lyrics_points = sum(float(d["points"]) for d in lyric_dims)
    lyrics_full_score = float(sum(lyric_maxima))
    lyrics_score = lyrics_points / lyrics_full_score * 100
    tee = _tee(is_cantonese, lyrics_score, pac_raw, tsmi_raw, nsrq_raw, c3ac_raw)
    b_offset = 0.0
    e_bonus = 0.0
    final = _clamp((style_score*0.4 + lyrics_score*0.6 + tee["TEE_offset"] + b_offset + e_bonus) / 100.0) * 100
    grade = "S+" if final >= 95 else "S" if final >= 85 else "A" if final >= 75 else "B" if final >= 60 else "C"

    risks: list[dict[str, Any]] = []
    for dimension in style_dims + lyric_dims:
        threshold = 2 if dimension["priority"] == "Core" else 3
        if dimension["raw"] <= threshold:
            severity = "high" if dimension["priority"] == "Core" else "medium" if dimension["priority"] == "Important" else "low"
            risks.append({"severity": severity, "dimension": dimension["code"], "message": f"{dimension['name']} 得分偏低", "raw": dimension["raw"]})
    if tee["CriticalPenalty_TSMI"] > 0: risks.append({"severity": "high", "dimension": "TSMI", "message": "声调方向错位触发 CriticalPenalty", "penalty": round(tee["CriticalPenalty_TSMI"], 2)})
    if tee["CriticalPenalty_PAC"] > 0: risks.append({"severity": "high", "dimension": "PAC", "message": "物象寄生不足触发 CriticalPenalty", "penalty": round(tee["CriticalPenalty_PAC"], 2)})
    if tee["CriticalPenalty_C3AC"] > 0: risks.append({"severity": "high", "dimension": "C3AC", "message": "最终副歌弧线不足触发 CriticalPenalty", "penalty": round(tee["CriticalPenalty_C3AC"], 2)})
    if generic_image_hits >= 2: risks.append({"severity": "medium", "dimension": "bias", "message": "命中多个高频 AI 意象，建议替换为具体日常物象", "hits": generic_image_hits})
    if not styles.strip() or not lyrics.strip(): risks.append({"severity": "high", "dimension": "input", "message": "Styles 或 Lyrics 为空"})

    rounded_tee = {key: round(value, 4) for key, value in tee.items()}
    return {
        "engine_version": ENGINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "language_profile": "cantonese" if is_cantonese else "mandarin",
        "score": round(final, 2),
        "grade": grade,
        "styles_score": round(style_score, 2),
        "lyrics_score": round(lyrics_score, 2),
        "lyrics_points": round(lyrics_points, 2),
        "lyrics_full_score": int(lyrics_full_score),
        "dimensions": style_dims + lyric_dims,
        "variables": rounded_tee,
        "feature_evidence": {"PAC": pac_evidence, "TSMI": tsmi_evidence, "NSRQ": nsrq_evidence, "C3AC": c3ac_evidence},
        "offsets": {"TEE_offset": round(tee["TEE_offset"], 4), "B_offset": b_offset, "E_bonus": e_bonus},
        "risks": risks[:16],
        "limitations": [
            "Frozen v2.1-Final mathematics are exact; lexical extraction is a deterministic reference implementation.",
            "TSMI uses a small auditable tone lexicon. Unknown hook finals fall back to neutral and are marked low-confidence.",
            "Production calibration must compare scores with A/B selection, Master conversion, refunds and rights-ready outcomes.",
        ],
    }
