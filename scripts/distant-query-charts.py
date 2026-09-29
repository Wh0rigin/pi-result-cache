#!/usr/bin/env python3
"""Create charts and a Markdown report for distant repeated queries."""

from __future__ import annotations

import json
import math
import statistics
import argparse
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "distant-query-results.json"
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"
REPORT_OUTPUT = DOCS / "distant-query-validation.md"
ASSET_PREFIX = "distant-query"
BASELINE = "baseline"
DEDUPE = "dedupe"
ARM_ORDER = [BASELINE, DEDUPE]
COLORS = {BASELINE: "#426B9A", DEDUPE: "#2E8065"}
LABELS = {BASELINE: "Pi 基线", DEDUPE: "结果去重"}

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.family": "SimHei",
    "axes.unicode_minus": False,
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 10,
    "figure.dpi": 140,
    "savefig.dpi": 180,
    "savefig.bbox": "tight",
})


def load_results() -> tuple[dict, list[dict]]:
    if not RESULTS.exists():
        raise SystemExit(f"Missing experiment results: {RESULTS}. Run `npm run benchmark:distance` first.")
    report = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = report.get("results", [])
    if not rows:
        raise SystemExit("The distant-query result file has no runs to plot.")
    return report, rows


def rows_for(rows: list[dict], arm: str, gap: int | None = None) -> list[dict]:
    return [row for row in rows if row.get("arm") == arm and (gap is None or row.get("gap") == gap)]


def paired_rows(rows: list[dict], gap: int) -> tuple[list[int], dict[int, dict], dict[int, dict]]:
    baseline = {row["seed"]: row for row in rows_for(rows, BASELINE, gap)}
    dedupe = {row["seed"]: row for row in rows_for(rows, DEDUPE, gap)}
    seeds = sorted(set(baseline) & set(dedupe))
    return seeds, baseline, dedupe


def mean_ci(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, 0.0
    t_critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}.get(len(values), 1.96)
    return mean, t_critical * statistics.stdev(values) / math.sqrt(len(values))


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) / total) + (z * z / (4 * total * total))) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def source_caption(report: dict) -> str:
    return (
        f"来源：Pi JSON usage 事件 · {report.get('model', 'model unavailable')} · "
        f"Pi {report.get('piVersion', 'unknown')} · 每个条件 {report.get('repetitionsPerCondition', '?')} 个种子 · "
        f"生成时间 {report.get('generatedAt', 'unknown')}"
    )


def add_caption(ax: plt.Axes, caption: str, width: int = 112) -> None:
    ax.text(0, -0.2, textwrap.fill(caption, width=width), transform=ax.transAxes, fontsize=8, color="#555555", va="top")


def save(fig: plt.Figure, filename: str) -> None:
    fig.savefig(ASSETS / asset_name(filename))
    plt.close(fig)


def asset_name(filename: str) -> str:
    return filename.replace("distant-query-", f"{ASSET_PREFIX}-", 1)


def fig_prompt_by_gap(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 5.3))
    x = np.arange(len(gaps))
    width = 0.34
    for offset, arm in enumerate(ARM_ORDER):
        means: list[float] = []
        errors: list[float] = []
        for gap in gaps:
            values = [float(row.get("promptTokensProcessed", 0)) for row in rows_for(rows, arm, gap)]
            mean, ci = mean_ci(values)
            means.append(mean)
            errors.append(ci)
        positions = x + (offset - 0.5) * width
        bars = ax.bar(positions, means, width, yerr=errors, capsize=5, color=COLORS[arm], label=LABELS[arm])
        for bar, mean, error in zip(bars, means, errors):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + error + max(means) * 0.018, f"{mean:,.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x, [str(value) for value in gaps])
    ax.set_xlabel("两次 target 查询之间的 filler 查询次数")
    ax.set_ylabel("处理的 prompt token（输入 + 缓存）")
    ax.set_title("查询间隔变远时的 prompt token 流量")
    ax.legend(frameon=False, loc="upper left")
    ax.set_ylim(bottom=0)
    add_caption(ax, source_caption(report))
    fig.tight_layout()
    save(fig, "distant-query-prompt-by-gap.png")


def fig_savings(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 5.1))
    means: list[float] = []
    errors: list[float] = []
    for index, gap in enumerate(gaps):
        seeds, baseline, dedupe = paired_rows(rows, gap)
        values = [
            100 * (baseline[seed].get("promptTokensProcessed", 0) - dedupe[seed].get("promptTokensProcessed", 0))
            / baseline[seed].get("promptTokensProcessed", 1)
            for seed in seeds
            if baseline[seed].get("promptTokensProcessed", 0) > 0
        ]
        mean, ci = mean_ci(values)
        means.append(mean)
        errors.append(ci)
        jitter = np.linspace(-0.08, 0.08, len(values)) if values else []
        ax.scatter(np.full(len(values), index) + jitter, values, color=COLORS[DEDUPE], s=55, alpha=0.78, edgecolor="white", linewidth=0.8)
        ax.errorbar(index, mean, yerr=ci, color="#1f2937", fmt="o", capsize=5, markersize=7, zorder=4)
        if values:
            ax.text(index, mean + ci + 1.7, f"{mean:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.plot(np.arange(len(gaps)), means, color=COLORS[DEDUPE], linewidth=2, alpha=0.85, label="平均配对减少")
    ax.axhline(0, color="#777777", linewidth=1)
    ax.set_xticks(np.arange(len(gaps)), [str(value) for value in gaps])
    ax.set_xlabel("filler 查询次数")
    ax.set_ylabel("prompt token 减少比例（%）")
    ax.set_title("两次相同查询间隔变远时的节省比例")
    ax.set_ylim(bottom=min(0, min(means, default=0) - 10))
    ax.legend(frameon=False, loc="lower right")
    add_caption(ax, "圆点表示各个配对种子；黑点和误差线表示平均值及 95% t 区间。" + source_caption(report))
    fig.tight_layout()
    save(fig, "distant-query-savings-by-gap.png")


def cumulative_trajectory(rows: list[dict], arm: str, gap: int) -> tuple[list[int], list[float]]:
    samples = []
    for row in rows_for(rows, arm, gap):
        request_usage = row.get("requestUsage", [])
        samples.append(np.cumsum([item.get("promptTokensProcessed", 0) for item in request_usage]))
    if not samples:
        return [], []
    request_count = min(len(sample) for sample in samples)
    values = [statistics.mean(float(sample[index]) for sample in samples) for index in range(request_count)]
    return list(range(1, request_count + 1)), values


def fig_trajectory(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, axes = plt.subplots(1, len(gaps), figsize=(5.2 * len(gaps), 4.4), squeeze=False)
    for index, gap in enumerate(gaps):
        ax = axes[0][index]
        for arm in ARM_ORDER:
            x, y = cumulative_trajectory(rows, arm, gap)
            if x:
                ax.plot(x, y, marker="o", markersize=3.5, linewidth=2, color=COLORS[arm], label=LABELS[arm])
        ax.set_title(f"间隔 {gap} 次 filler 查询")
        ax.set_xlabel("模型请求序号")
        if index == 0:
            ax.set_ylabel("累计 prompt token")
        ax.set_ylim(bottom=0)
        if index == len(gaps) - 1:
            ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.suptitle("两次 target 查询之间的累计 prompt token 轨迹", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, textwrap.fill(source_caption(report), width=180), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "distant-query-cumulative-trajectory.png")


def fig_suppression(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14.0, 4.8))
    for axis, field, ylabel, title in zip(
        axes,
        ["duplicateOccurrencesSuppressed", "estimatedTokensSuppressed", "expiredOccurrences"],
        ["平均重复结果替换次数", "平均估算抑制 token", "平均过期次数"],
        ["间隔变远后的插件命中次数", "字符估算的抑制量", "达到阈值后过期的结果"],
    ):
        x = np.arange(len(gaps))
        values = []
        errors = []
        for gap in gaps:
            sample = [row.get(field, 0) for row in rows_for(rows, DEDUPE, gap)]
            mean, ci = mean_ci([float(value) for value in sample])
            values.append(mean)
            errors.append(ci)
        bars = axis.bar(x, values, yerr=errors, capsize=5, color=COLORS[DEDUPE], width=0.58)
        for bar, value in zip(bars, values):
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values, default=1) * 0.025, f"{value:,.0f}", ha="center", va="bottom", fontsize=9)
        axis.set_title(title)
        axis.set_xticks(x, [str(value) for value in gaps])
        axis.set_xlabel("filler 查询次数")
        axis.set_ylabel(ylabel)
        axis.set_ylim(bottom=0)
    fig.suptitle("间隔拉长时插件的重复结果命中与过期", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, "估算抑制 token = 字符数 / 4，仅作诊断；账单应以供应商价格和 Pi usage 字段为准。 " + source_caption(report), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "distant-query-suppression-metrics.png")


def fig_quality(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.8), sharey=True)
    for axis, field, title in zip(axes, ["answerCorrect", "protocolCompliant"], ["最终答案准确率", "完整查询序列遵守率"]):
        x = np.arange(len(gaps))
        width = 0.34
        for offset, arm in enumerate(ARM_ORDER):
            rates = []
            lower = []
            upper = []
            for gap in gaps:
                sample = rows_for(rows, arm, gap)
                successes = sum(bool(row.get(field)) for row in sample)
                rate = successes / len(sample) if sample else 0
                low, high = wilson_interval(successes, len(sample))
                rates.append(rate)
                lower.append(rate - low)
                upper.append(high - rate)
            positions = x + (offset - 0.5) * width
            bars = axis.bar(positions, rates, width=width, yerr=[lower, upper], capsize=4, color=COLORS[arm], label=LABELS[arm])
            for bar, gap in zip(bars, gaps):
                sample = rows_for(rows, arm, gap)
                successes = sum(bool(row.get(field)) for row in sample)
                axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.04, f"{successes}/{len(sample)}", ha="center", va="bottom", fontsize=8)
        axis.set_title(title)
        axis.set_xticks(x, [str(value) for value in gaps])
        axis.set_xlabel("filler 查询次数")
        axis.set_ylim(0, 1.28)
        axis.legend(frameon=False, fontsize=8, loc="upper left")
    axes[0].set_ylabel("成功率")
    fig.suptitle("查询间隔变远时的正确性与协议检查", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, "柱高表示观测成功率；误差线表示 Wilson 95% 区间。" + source_caption(report), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "distant-query-quality.png")


def fig_protocol_components(report: dict, rows: list[dict], gaps: list[int]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.8))
    for axis, field, expected, title in zip(
        axes,
        ["targetReadCalls", "fillerReadCalls"],
        [2, None],
        ["目标文件实际读取次数", "filler 文件实际读取次数"],
    ):
        x = np.arange(len(gaps))
        width = 0.34
        for offset, arm in enumerate(ARM_ORDER):
            means = []
            errors = []
            for gap in gaps:
                values = [float(row.get(field, 0)) for row in rows_for(rows, arm, gap)]
                mean, ci = mean_ci(values)
                means.append(mean)
                errors.append(ci)
            positions = x + (offset - 0.5) * width
            axis.bar(positions, means, width=width, yerr=errors, capsize=4, color=COLORS[arm], label=LABELS[arm])
        if expected is not None:
            axis.axhline(expected, color="#777777", linestyle="--", linewidth=1, label="期望次数")
        else:
            axis.plot(x, gaps, color="#777777", linestyle="--", linewidth=1, marker="o", label="期望次数")
        axis.set_title(title)
        axis.set_xticks(x, [str(value) for value in gaps])
        axis.set_xlabel("filler 查询次数")
        axis.set_ylabel("平均工具调用次数")
        axis.set_ylim(bottom=0)
        axis.legend(frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("长间隔实验中的工具调用漂移", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, "柱高为各条件平均调用次数；虚线为任务要求。" + source_caption(report), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "distant-query-protocol-components.png")


def format_metric(value: float) -> str:
    return f"{value:,.0f}"


def make_report(report: dict, rows: list[dict], gaps: list[int]) -> None:
    expire_after = report.get("expireAfterToolResults")
    title = "# 两次查询间隔过期策略实验验证" if "expiry" in ASSET_PREFIX else "# 两次查询间隔实验验证"
    gap_summary = " 或 ".join(str(gap) for gap in sorted({int(row["gap"]) for row in rows}))
    lines = [
        title,
        "",
        f"- 生成时间：{report.get('generatedAt', 'unknown')}",
        f"- Pi 模型：`{report.get('model', 'unknown')}`；Pi 版本：`{report.get('piVersion', 'unknown')}`；每个间隔条件 {report.get('repetitionsPerCondition', '?')} 个种子配对。",
        f"- 任务：先读取 `target.txt`，按条件插入 {gap_summary} 个不同 filler 文件，再次读取 `target.txt` 并提取目标 token。目标文件 {report.get('fixture', {}).get('targetRows', '?')} 行，每个 filler 文件 {report.get('fixture', {}).get('fillerRows', '?')} 行。",
        "- 这里用中间查询次数模拟“对话间隔较远”；它测试上下文中间插入多轮内容的效果，不等同于让程序空等一段墙钟时间。",
        "- 基线关闭插件；去重组加载插件。两组使用同一模型、提示、只读工具权限和文件种子。",
        (
            f"- 过期规则：达到 {report['expireAfterToolResults']} 个中间工具结果（对应实验中的 filler 查询）时，保留当前完整结果并重置锚点。"
            if report.get("expireAfterToolResults") is not None
            else "- 此数据集没有设置间隔上限，记录的是按当时原实现进行的历史对照。"
        ),
        "",
        "## 结果摘要",
        "",
    ]

    for gap in gaps:
        seeds, baseline, dedupe = paired_rows(rows, gap)
        if not seeds:
            lines.append(f"- **间隔 {gap} 个 filler 查询**：没有完整配对数据。")
            continue
        base_prompt = statistics.mean(baseline[seed].get("promptTokensProcessed", 0) for seed in seeds)
        dedupe_prompt = statistics.mean(dedupe[seed].get("promptTokensProcessed", 0) for seed in seeds)
        reduction = 100 * (base_prompt - dedupe_prompt) / base_prompt if base_prompt else 0
        base_correct = sum(bool(baseline[seed].get("answerCorrect")) for seed in seeds)
        dedupe_correct = sum(bool(dedupe[seed].get("answerCorrect")) for seed in seeds)
        base_protocol = sum(bool(baseline[seed].get("protocolCompliant")) for seed in seeds)
        dedupe_protocol = sum(bool(dedupe[seed].get("protocolCompliant")) for seed in seeds)
        base_target = statistics.mean(baseline[seed].get("targetReadCalls", 0) for seed in seeds)
        dedupe_target = statistics.mean(dedupe[seed].get("targetReadCalls", 0) for seed in seeds)
        base_filler = statistics.mean(baseline[seed].get("fillerReadCalls", 0) for seed in seeds)
        dedupe_filler = statistics.mean(dedupe[seed].get("fillerReadCalls", 0) for seed in seeds)
        hits = statistics.mean(dedupe[seed].get("duplicateOccurrencesSuppressed", 0) for seed in seeds)
        expired = statistics.mean(dedupe[seed].get("expiredOccurrences", 0) for seed in seeds)
        estimate = statistics.mean(dedupe[seed].get("estimatedTokensSuppressed", 0) for seed in seeds)
        expiry_note = ""
        if expire_after is not None and gap >= expire_after:
            expiry_note = (
                f"；该间隔达到 {expire_after} 次过期阈值，完整重复结果被保留，扩展抑制为 0；"
                "端到端 prompt 总量的微小差异不应解释为插件节省"
            )
        lines.append(
            f"- **间隔 {gap} 个 filler 查询（n={len(seeds)}）**：平均 prompt token 从 {format_metric(base_prompt)} 降到 {format_metric(dedupe_prompt)}，减少 **{reduction:.1f}%**；准确率为基线 {base_correct}/{len(seeds)}、去重 {dedupe_correct}/{len(seeds)}；完整序列遵守率为基线 {base_protocol}/{len(seeds)}、去重 {dedupe_protocol}/{len(seeds)}；目标文件平均读取次数为基线 {base_target:.1f}、去重 {dedupe_target:.1f}，filler 平均读取次数为基线 {base_filler:.1f}、去重 {dedupe_filler:.1f}；去重组平均命中 {hits:,.0f} 次、过期 {expired:,.0f} 次，字符估算抑制约 {estimate:,.0f} token{expiry_note}。"
        )

    lines.extend([
        "",
        "## 图表",
        "",
        "### prompt token 流量",
        "",
        f"![Distant query prompt token traffic](../assets/{asset_name('distant-query-prompt-by-gap.png')})",
        "",
        "### 节省比例",
        "",
        f"![Distant query savings](../assets/{asset_name('distant-query-savings-by-gap.png')})",
        "",
        "### 每次模型请求的累计增长轨迹",
        "",
        f"![Distant query cumulative trajectory](../assets/{asset_name('distant-query-cumulative-trajectory.png')})",
        "",
        "### 去重命中和估算抑制量",
        "",
        f"![Distant query suppression metrics](../assets/{asset_name('distant-query-suppression-metrics.png')})",
        "",
        "### 正确性与完整序列遵守率",
        "",
        f"![Distant query quality](../assets/{asset_name('distant-query-quality.png')})",
        "",
        "### 目标与 filler 调用漂移",
        "",
        f"![Distant query protocol components](../assets/{asset_name('distant-query-protocol-components.png')})",
        "",
        "## 如何理解“遗忘”",
        "",
        "- 如果 Pi 仍把第一次 `target.txt` 结果保留在 `context` 事件的消息数组中，插件可以在最后一次 target 查询之后识别完全相同的结果；启用过期阈值后，超过阈值会保留新的完整副本来刷新上下文。",
        "- 如果 Pi 因上下文压缩、截断或其他策略已经移除了第一次结果，插件没有跨会话数据库，不能凭空恢复它；这类情况应通过 `protocol`、答案准确率和命中次数一起判断。",
        "- 过期上限是可配置的工程策略，不是模型记忆开始衰减的普适临界值；本实验只验证在 12 和 15 个 filler 查询处的边界行为。",
        "- 这个实验的主要变量是中间轮次数量，因此能说明“距离变远但上下文仍保留”时的作用；它不能单独证明任何供应商模型在真实长时间空闲后一定会遗忘。",
        "",
        "## 限制",
        "",
        "1. 每个条件只有少量种子；准确率相同或不同都不能替代更大规模的真实任务评估。",
        "2. 任务使用人为构造的目标文件和 filler 文件，真实对话中的重复查询、结果长度和上下文压缩策略会不同。",
        "3. 基线如果多调用了工具，端到端 token 差异会同时包含模型行为漂移；报告单独画出了实际调用次数。",
        "4. `chars/4` 是粗略诊断值；prompt token 图使用 Pi 的 `input + cacheRead + cacheWrite`，不等于账单金额。",
        "",
        f"逐次 usage、读取序列、答案、过期计数、协议检查和每次请求轨迹见 [`{RESULTS.name}`](../results/{RESULTS.name})。",
        "",
    ])
    if "expiry" not in ASSET_PREFIX:
        lines.extend([
            "受控过期阈值的 12 / 15 filler 边界实验见 [`distant-query-expiry-validation.md`](distant-query-expiry-validation.md)。",
            "",
        ])
    DOCS.mkdir(parents=True, exist_ok=True)
    REPORT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    global RESULTS, REPORT_OUTPUT, ASSET_PREFIX, LABELS
    parser = argparse.ArgumentParser(description="Chart distant-query Pi experiments.")
    parser.add_argument("--input", type=Path, default=RESULTS)
    parser.add_argument("--report", type=Path, default=REPORT_OUTPUT)
    parser.add_argument("--asset-prefix", default=ASSET_PREFIX)
    args = parser.parse_args()
    RESULTS = args.input if args.input.is_absolute() else ROOT / args.input
    REPORT_OUTPUT = args.report if args.report.is_absolute() else ROOT / args.report
    ASSET_PREFIX = args.asset_prefix
    ASSETS.mkdir(parents=True, exist_ok=True)
    report, rows = load_results()
    expire_after = report.get("expireAfterToolResults")
    if expire_after is not None:
        LABELS[DEDUPE] = f"结果去重（{expire_after} 个工具结果后过期）"
    gaps = sorted({int(row["gap"]) for row in rows})
    if not gaps:
        raise SystemExit("No gap conditions found in distant-query results.")
    fig_prompt_by_gap(report, rows, gaps)
    fig_savings(report, rows, gaps)
    fig_trajectory(report, rows, gaps)
    fig_suppression(report, rows, gaps)
    fig_quality(report, rows, gaps)
    fig_protocol_components(report, rows, gaps)
    make_report(report, rows, gaps)
    print(f"Generated 6 distant-query comparison charts and {REPORT_OUTPUT}")


if __name__ == "__main__":
    main()
