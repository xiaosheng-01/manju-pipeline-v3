#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
漫剧流程 Skill 3.0 —— 台词时长预检脚本（阶段 4 / 阶段 8 必跑）

规则口径（见 references/ch07a-dialogue.md 7.5、ch07b-dialogue-timing.md 7.19、
          references/ch12-storyboard-template.md 12.1）：
  字数 N        ：逐字统计。默认只计汉字、字母、数字，不含标点与空白；
                  可用 --count 手动覆盖（与人工口径不一致时以用户给定字数为准）。
  红线时长      ：N / 4.5 秒（4.5 字/秒，吞台词极限语速；超过必超窗）。
  目标时长      ：N / 3.5 + 节奏缓冲 + 情绪缓冲（3.5 字/秒舒适语速 + 缓冲；
                  可用 --target-rate 调整）。
  核心可用时间  ：镜头总时长 - 1.0（首帧 0.5s + 尾帧 0.5s）；
                  无铺垫段 --no-opening 只扣尾帧 0.5s。
  放得下判定    ：单句目标 ≤ 核心可用；多人镜头再要求 Σ各句目标 + Σ切换停顿 ≤ 核心可用。
  放不下处理顺序：压缩 → 改动作关系/切反应转画外音 → 当前镜跨子分镜 → 延长镜头(≤12s)
                  → 拆当前镜 → 最后才跨镜头（跨镜必须重排两镜）。

用法：
  # 单句
  python3 dialogue_time_check.py --shot 12 --line "风起青萍" --rhythm 0.3 --emotion 0.5

  # 单镜多句（--pause 为相邻两句之间的角色切换停顿，按顺序对应）
  python3 dialogue_time_check.py --shot 12 \
      --line "马刘，借支笔呗，我的没水了。" --speaker 夏小满 \
      --line "自己拿。" --speaker 马刘 --pause 0.5 \
      --line "谢啦。" --speaker 夏小满 --pause 2

  # 手动指定字数（口径覆盖）、说话人、无铺垫段
  python3 dialogue_time_check.py --shot 11 --no-opening --line "长台词……" --count 31

  # 舒适目标语速默认 3.5；如需旧口径可显式传 3.0
  python3 dialogue_time_check.py --shot 12 --target-rate 3.0 --line "……"

  # JSON 批量（每句可单独给 rhythm/emotion/count；顶层可给 redline_rate/target_rate）
  python3 dialogue_time_check.py --json '{"shot":12,"lines":[{"speaker":"A","text":"…","rhythm":0.5,"emotion":0.5}],"pauses":[0.5]}'

退出码：全部放得下 0；存在放不下 1（可用于流水线判定）。
"""
import argparse
import json
import re
import sys

CHAR_RE = re.compile(r"[一-鿿A-Za-z0-9]")


def count_chars(text: str) -> int:
    """逐字统计：汉字/字母/数字计 1 字，标点与空白不计。"""
    return len(CHAR_RE.findall(text))


def r1(x: float) -> str:
    """保留两位小数并去尾零，便于阅读。"""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def check(shot: float, lines: list, pauses: list, no_opening: bool,
          rhythm: float, emotion: float, redline_rate: float = 4.5,
          target_rate: float = 3.5, verbose: bool = True):
    core = shot - (0.5 if no_opening else 1.0)
    results = []
    for i, ln in enumerate(lines):
        text = ln["text"]
        n = int(ln.get("count") or count_chars(text))
        rh = float(ln.get("rhythm", rhythm))
        em = float(ln.get("emotion", emotion))
        redline = n / redline_rate
        target = n / target_rate + rh + em
        fit_single = target <= core
        results.append({
            "idx": i + 1, "speaker": ln.get("speaker", f"角色{i+1}"),
            "text": text, "n": n, "rh": rh, "em": em,
            "redline": redline, "target": target, "fit_single": fit_single,
        })

    total_speech = sum(x["target"] for x in results)
    total_pause = sum(pauses[: max(0, len(results) - 1)])
    total = total_speech + total_pause
    fit_all = total <= core and all(x["fit_single"] for x in results)

    if verbose:
        tail_note = "（无铺垫段，仅扣尾帧0.5s）" if no_opening else "（首帧0.5s+尾帧0.5s）"
        print(f"=== 台词时长预检｜镜头总时长 {r1(shot)}s｜核心可用 {r1(core)}s {tail_note} ===")
        print(f"口径：红线 {r1(redline_rate)} 字/秒（极限）｜目标 {r1(target_rate)} 字/秒（舒适）+缓冲")
        for x in results:
            print(f"[{x['idx']}] {x['speaker']} 「{x['text']}」")
            print(f"    字数 {x['n']}（不含标点）"
                  f"｜红线 {r1(x['redline'])}s（字数÷{r1(redline_rate)}）"
                  f"｜目标 {r1(x['target'])}s（字数÷{r1(target_rate)}={r1(x['n']/target_rate)} + 节奏{x['rh']} + 情绪{x['em']}）")
            if x["fit_single"]:
                print(f"    单句判定：放得下（目标 {r1(x['target'])}s ≤ 核心可用 {r1(core)}s）")
            else:
                cap_comfort = int((core - x["rh"] - x["em"]) * target_rate)
                cap_red = int(core * redline_rate)
                print(f"    单句判定：【放不下】目标 {r1(x['target'])}s > 核心可用 {r1(core)}s")
                print(f"    建议：舒适口径（{r1(target_rate)}字/秒+缓冲）本镜单句≤{cap_comfort}字，"
                      f"红线极限（{r1(redline_rate)}字/秒）≤{cap_red}字；"
                      f"优先压缩台词，其次当前镜跨子分镜（台词锚仍写整句起止、画面切反应/环境/道具），"
                      f"再次延长镜头至≤12s，最后才拆镜/跨镜头（须重排两镜）。")
        if len(results) > 1:
            print("-" * 64)
            print(f"多人镜头合计：各句目标 Σ {r1(total_speech)}s + 切换停顿 Σ {r1(total_pause)}s "
                  f"= {r1(total)}s（核心可用 {r1(core)}s）")
        print("-" * 64)
        if fit_all:
            print(f"结论：✅ 预检通过，可写完整分镜。台词锚写整句起止，子分镜末尾标注说到哪几个字。")
        else:
            print("结论：❌ 预检未通过，禁止写完整分镜；按七级顺序处理后重新预检。")
            over = total - core
            if over > 0 and len(results) > 1:
                print(f"  （合计超出 {r1(over)}s：压缩台词、把部分台词切给反应/环境镜头转画外音，或拆为两个镜头并重排两镜。）")
    return {"core": core, "lines": results, "total": total, "fit": fit_all}


def main():
    ap = argparse.ArgumentParser(
        description="漫剧分镜台词时长预检（红线=字数/4.5，目标=字数/3.5+缓冲；语速可调）")
    ap.add_argument("--shot", type=float, default=12, help="镜头总时长（秒），10-12 整数秒，默认 12")
    ap.add_argument("--no-opening", action="store_true", help="无铺垫段：只扣尾帧 0.5s")
    ap.add_argument("--redline-rate", type=float, default=4.5,
                    help="红线极限语速（字/秒），默认 4.5")
    ap.add_argument("--target-rate", type=float, default=3.5,
                    help="舒适目标语速（字/秒），默认 3.5")
    ap.add_argument("--rhythm", type=float, action="append", default=[],
                    help="节奏缓冲（秒）；可跟在某句 --line 后按句指定，也可全局给一次，默认 0.3")
    ap.add_argument("--emotion", type=float, action="append", default=[],
                    help="情绪缓冲（秒）；可跟在某句 --line 后按句指定，也可全局给一次，默认 0.5")
    ap.add_argument("--line", action="append", default=[], help="台词原文，可重复；按说话顺序")
    ap.add_argument("--speaker", action="append", default=[], help="对应 --line 的说话人，可重复")
    ap.add_argument("--count", action="append", type=int, default=[], help="手动覆盖对应 --line 的字数，可重复")
    ap.add_argument("--pause", action="append", type=float, default=[],
                    help="相邻两句之间的角色切换停顿（秒），第1个对应 line1→line2，可重复")
    ap.add_argument("--json", dest="json_str", help="JSON 批量输入（shot/no_opening/redline_rate/target_rate/lines/pauses）")
    args = ap.parse_args()

    def pick(seq, i, default):
        return seq[i] if i < len(seq) else (seq[0] if len(seq) == 1 else default)

    if args.json_str:
        data = json.loads(args.json_str)
        result = check(
            float(data.get("shot", 12)),
            data.get("lines", []),
            [float(x) for x in data.get("pauses", [])],
            bool(data.get("no_opening", False)),
            args.rhythm if args.rhythm else 0.3,
            args.emotion if args.emotion else 0.5,
            float(data.get("redline_rate", args.redline_rate)),
            float(data.get("target_rate", args.target_rate)),
        )
    else:
        if not args.line:
            ap.error("至少用 --line 传入一句台词，或用 --json 批量输入。")
        lines = []
        for i, text in enumerate(args.line):
            ln = {"text": text,
                  "rhythm": pick(args.rhythm, i, 0.3),
                  "emotion": pick(args.emotion, i, 0.5)}
            if i < len(args.speaker):
                ln["speaker"] = args.speaker[i]
            if i < len(args.count):
                ln["count"] = args.count[i]
            lines.append(ln)
        result = check(args.shot, lines, args.pause, args.no_opening,
                       pick(args.rhythm, 0, 0.3), pick(args.emotion, 0, 0.5),
                       args.redline_rate, args.target_rate)

    sys.exit(0 if result["fit"] else 1)


if __name__ == "__main__":
    main()
