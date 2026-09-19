#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
漫剧流程 Skill 5.0 —— 台词时长预检脚本（阶段 4 / 阶段 8 必跑）

规则口径（见 references/ch07a-dialogue.md 7.0/7.5、ch07b-dialogue-timing.md 7.19）：
  字数 N          ：逐字统计。默认只计汉字、字母、数字，不含标点与空白；
                    可用 --count 手动覆盖（与人工口径不一致时以用户给定字数为准）。
  情绪缓冲（按句末标点查表，可用 --emotion 手动覆盖）：
                    5字及以下 0s｜6-10字 0.4s｜11-20字 0.75s｜21-30字 1.25s｜31字及以上 1.75s
  情绪缓冲（自动按情绪类型分级，可用 --mood 选择，或 --emotion 手动覆盖）：
                    日常平静 0s｜激动愤怒 0.5s｜恐惧犹豫 0.5s｜悲伤哭泣 0.75s｜高燃打脸 0.75s｜温柔告白 0.75s
  红线纯发声时长  ：N / 4.5 秒（4.5 字/秒，吞台词极限语速）。
  红线总时长      ：N / 4.5 + 情绪缓冲（按句末标点查表）。
  目标纯发声时长  ：N / 3.5 秒（3.5 字/秒舒适语速）。
  目标总时长      ：N / 3.5 + 节奏缓冲 + 情绪缓冲。
  核心可用时间    ：镜头总时长 - 1.0（首帧 0.5s + 尾帧 0.5s）；
                    无铺垫段 --no-opening 只扣尾帧 0.5s。
  放得下判定      ：核心可用 ≥ 红线总时长 → 放得下；核心可用 < 红线总时长 → 放不下。
  语速档位        ：红线总时长 ≤ 核心可用 < 目标总时长 → 语速偏快；核心可用 ≥ 目标总时长 → 语速正常。
  放不下处理顺序  ：压缩 → 改动作关系/切反应转画外音 → 当前镜跨子分镜 → 延长镜头(≤12s)
                    → 拆当前镜 → 最后才跨镜头（跨镜必须重排两镜）。

用法：
  # 单句（情绪缓冲按句末标点查表，默认0.5）
  python3 dialogue_time_check.py --shot 12 --line "风起青萍"

  # 单句指定情绪类型（自动算情绪缓冲）
  python3 dialogue_time_check.py --shot 12 --line "小杂种！本座要你碎尸万段！" --mood 激动愤怒

  # 单镜多句（--pause 为相邻两句之间的角色切换停顿，按顺序对应）
  python3 dialogue_time_check.py --shot 12 \
      --line "马刘，借支笔呗，我的没水了。" --speaker 夏小满 --mood 日常平静 \
      --line "自己拿。" --speaker 马刘 --pause 0.5 --mood 日常平静 \
      --line "谢啦。" --speaker 夏小满 --pause 2 --mood 日常平静

  # 手动覆盖节奏/情绪缓冲（秒）
  python3 dialogue_time_check.py --shot 12 --line "长台词……" --rhythm 1.25 --emotion 0.75

  # JSON 批量（每句可单独给 rhythm/emotion/mood/count；顶层可给 redline_rate/target_rate）
  python3 dialogue_time_check.py --json '{"shot":12,"lines":[{"speaker":"A","text":"…","mood":"高燃打脸"}],"pauses":[0.5]}'

退出码：全部放得下 0；存在放不下 1（可用于流水线判定）。
"""
import argparse
import json
import re
import sys

CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")

# 情绪缓冲按句末标点查表，见ch07a第7.1.6.2
def auto_rhythm(n: int) -> float:
    if n <= 5:
        return 0.0
    elif n <= 10:
        return 0.4
    elif n <= 20:
        return 0.75
    elif n <= 30:
        return 1.25
    else:
        return 1.75

# 情绪缓冲按类型分级
MOOD_MAP = {
    "日常平静": 0.0,
    "激动愤怒": 0.5,
    "恐惧犹豫": 0.5,
    "悲伤哭泣": 0.75,
    "高燃打脸": 0.75,
    "温柔告白": 0.75,
}

def auto_emotion(mood: str) -> float:
    return MOOD_MAP.get(mood, 0.0)


def count_chars(text: str) -> int:
    """逐字统计：汉字/字母/数字计 1 字，标点与空白不计。"""
    return len(CHAR_RE.findall(text))


def r1(x: float) -> str:
    """只精确到0.1秒（四舍五入），禁止0.01秒级精度。"""
    return f"{x:.1f}"


def check(shot: float, lines: list, pauses: list, no_opening: bool,
          redline_rate: float = 4.5, target_rate: float = 3.5,
          verbose: bool = True):
    core = shot - (0.5 if no_opening else 1.0)
    results = []
    for i, ln in enumerate(lines):
        text = ln["text"]
        n = int(ln.get("count") or count_chars(text))
        # 情绪缓冲：手动指定优先，否则按句末标点查表
        rh = float(ln["rhythm"]) if ln.get("rhythm") is not None else auto_rhythm(n)
        # 情绪缓冲：手动指定优先，否则按情绪类型自动分级
        if ln.get("emotion") is not None:
            em = float(ln["emotion"])
        else:
            em = auto_emotion(ln.get("mood", "日常平静"))
        redline_pure = n / redline_rate
        redline_total = redline_pure + rh + em
        target_pure = n / target_rate
        target_total = target_pure + rh + em
        # 放得下：核心可用 >= 红线总时长
        fit_single = core >= redline_total
        # 语速档位
        if fit_single and core < target_total:
            speed_gear = "语速偏快"
        elif fit_single:
            speed_gear = "语速正常"
        else:
            speed_gear = "放不下"
        results.append({
            "idx": i + 1, "speaker": ln.get("speaker", f"角色{i+1}"),
            "text": text, "n": n, "rh": rh, "em": em,
            "mood": ln.get("mood", "日常平静"),
            "redline_pure": redline_pure, "redline_total": redline_total,
            "target_pure": target_pure, "target_total": target_total,
            "fit_single": fit_single, "speed_gear": speed_gear,
        })

    total_speech = sum(x["target_total"] for x in results)
    total_pause = sum(pauses[: max(0, len(results) - 1)])
    total = total_speech + total_pause
    fit_all = total <= core and all(x["fit_single"] for x in results)

    if verbose:
        tail_note = "（无铺垫段，仅扣尾帧0.5s）" if no_opening else "（首帧0.5s+尾帧0.5s）"
        print(f"=== 台词时长预检｜镜头总时长 {r1(shot)}s｜核心可用 {r1(core)}s {tail_note} ===")
        print(f"口径：红线 {r1(redline_rate)} 字/秒（极限）｜目标 {r1(target_rate)} 字/秒（舒适）")
        print(f"缓冲：节奏按字数自动分级｜情绪按类型自动分级（可用--rhythm/--emotion手动覆盖）")
        for x in results:
            print(f"[{x['idx']}] {x['speaker']} 「{x['text']}」")
            print(f"    字数 {x['n']}（不含标点）｜情绪类型：{x['mood']}")
            print(f"    情绪缓冲 {r1(x['em'])}s（按句末标点查表）")
            print(f"    红线纯发声 {r1(x['redline_pure'])}s（字数÷{r1(redline_rate)}）"
                  f"｜红线总时长 {r1(x['redline_total'])}s（纯发声+节奏+情绪）")
            print(f"    目标纯发声 {r1(x['target_pure'])}s（字数÷{r1(target_rate)}）"
                  f"｜目标总时长 {r1(x['target_total'])}s（纯发声+节奏+情绪）")
            if x["fit_single"]:
                print(f"    单句判定：✅ 放得下（红线总时长 {r1(x['redline_total'])}s ≤ 核心可用 {r1(core)}s）｜{x['speed_gear']}")
            else:
                cap_redline = int((core - x["rh"] - x["em"]) * redline_rate)
                cap_target = int((core - x["rh"] - x["em"]) * target_rate)
                print(f"    单句判定：❌ 放不下（红线总时长 {r1(x['redline_total'])}s > 核心可用 {r1(core)}s）")
                print(f"    建议：本镜单句红线极限≤{cap_redline}字，舒适目标≤{cap_target}字；"
                      f"优先压缩台词，其次当前镜跨子分镜（台词锚仍写整句起止、画面切反应/环境/道具），"
                      f"再次延长镜头至≤12s，最后才拆镜/跨镜头（须重排两镜）。")
        if len(results) > 1:
            print("-" * 64)
            print(f"多人镜头合计：各句目标总时长 Σ {r1(total_speech)}s + 切换停顿 Σ {r1(total_pause)}s "
                  f"= {r1(total)}s（核心可用 {r1(core)}s）")
        print("-" * 64)
        if fit_all:
            print(f"结论：✅ 预检通过，可写完整分镜。台词锚写整句起止，子分镜末尾标注说到哪几个字。")
        else:
            print(f"结论：❌ 预检未通过，禁止写完整分镜；按处理顺序压缩/跨子分镜/延长/拆镜后重新预检。")
            over = total - core
            if over > 0 and len(results) > 1:
                print(f"  （合计超出 {r1(over)}s：压缩台词、把部分台词切给反应/环境镜头转画外音，或拆为两个镜头并重排两镜。）")
    return {"core": core, "lines": results, "total": total, "fit": fit_all}


def main():
    ap = argparse.ArgumentParser(
        description="漫剧分镜台词时长预检（5.0版：节奏按字数分级/情绪按类型分级/四时长/红线总时长判定）")
    ap.add_argument("--shot", type=float, default=12, help="镜头总时长（秒），10-12 整数秒，默认 12")
    ap.add_argument("--no-opening", action="store_true", help="无铺垫段：只扣尾帧 0.5s")
    ap.add_argument("--redline-rate", type=float, default=4.5,
                    help="红线极限语速（字/秒），默认 4.5")
    ap.add_argument("--target-rate", type=float, default=3.5,
                    help="舒适目标语速（字/秒），默认 3.5")
    ap.add_argument("--rhythm", type=float, action="append", default=[],
                    help="节奏缓冲（秒）手动覆盖；可跟在某句 --line 后按句指定；不指定则按字数自动分级")
    ap.add_argument("--emotion", type=float, action="append", default=[],
                    help="情绪缓冲（秒）手动覆盖；可跟在某句 --line 后按句指定；不指定则按--mood自动分级")
    ap.add_argument("--mood", action="append", default=[],
                    help="情绪类型：日常平静/激动愤怒/恐惧犹豫/悲伤哭泣/高燃打脸/温柔告白；可跟在某句 --line 后按句指定，默认日常平静")
    ap.add_argument("--line", action="append", default=[], help="台词原文，可重复；按说话顺序")
    ap.add_argument("--speaker", action="append", default=[], help="对应 --line 的说话人，可重复")
    ap.add_argument("--count", action="append", type=int, default=[], help="手动覆盖对应 --line 的字数，可重复")
    ap.add_argument("--pause", action="append", type=float, default=[],
                    help="相邻两句之间的角色切换停顿（秒），第1个对应 line1→line2，可重复")
    ap.add_argument("--json", dest="json_str", help="JSON 批量输入（shot/no_opening/redline_rate/target_rate/lines/pauses）")
    args = ap.parse_args()

    def pick(seq, i, default=None):
        return seq[i] if i < len(seq) else (seq[0] if len(seq) == 1 else default)

    if args.json_str:
        data = json.loads(args.json_str)
        result = check(
            float(data.get("shot", 12)),
            data.get("lines", []),
            [float(x) for x in data.get("pauses", [])],
            bool(data.get("no_opening", False)),
            float(data.get("redline_rate", args.redline_rate)),
            float(data.get("target_rate", args.target_rate)),
        )
    else:
        if not args.line:
            ap.error("至少用 --line 传入一句台词，或用 --json 批量输入。")
        lines = []
        for i, text in enumerate(args.line):
            ln = {"text": text,
                  "rhythm": pick(args.rhythm, i),
                  "emotion": pick(args.emotion, i),
                  "mood": pick(args.mood, i, "日常平静")}
            if i < len(args.speaker):
                ln["speaker"] = args.speaker[i]
            if i < len(args.count):
                ln["count"] = args.count[i]
            lines.append(ln)
        result = check(args.shot, lines, args.pause, args.no_opening,
                       args.redline_rate, args.target_rate)

    sys.exit(0 if result["fit"] else 1)


if __name__ == "__main__":
    main()
