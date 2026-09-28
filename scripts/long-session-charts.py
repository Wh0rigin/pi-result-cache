#!/usr/bin/env python3
"""Create long-session comparison charts and a Markdown validation report."""

from __future__ import annotations

import json
import math
import statistics
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "long-session-results.json"
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"
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
        raise SystemExit(f"Missing experiment results: {RESULTS}. Run `npm run benchmark:long` first.")
    report = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = report.get("results", [])
    if not rows:
        raise SystemExit("The long-session result file has no runs to plot.")
    return report, rows


def rows_for(rows: list[dict], arm: str, repeats: int | None = None) -> list[dict]:
    return [
        row for row in rows
        if row.get("arm") == arm and (repeats is None or row.get("repeats") == repeats)
    ]


def paired_rows(rows: list[dict], repeats: int) -> tuple[list[int], dict[int, dict], dict[int, dict]]:
    baseline = {row["seed"]: row for row in rows_for(rows, BASELINE, repeats)}
    dedupe = {row["seed"]: row for row in rows_for(rows, DEDUPE, repeats)}
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
    fig.savefig(ASSETS / filename)
    plt.close(fig)


def fig_prompt_by_length(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 5.3))
    x = np.arange(len(repeats_list))
    width = 0.34
    for offset, arm in enumerate(ARM_ORDER):
        means: list[float] = []
        errors: list[float] = []
        for repeats in repeats_list:
            values = [float(row.get("promptTokensProcessed", 0)) for row in rows_for(rows, arm, repeats)]
            mean, ci = mean_ci(values)
            means.append(mean)
            errors.append(ci)
        positions = x + (offset - 0.5) * width
        bars = ax.bar(positions, means, width, yerr=errors, capsize=5, color=COLORS[arm], label=LABELS[arm])
        for bar, mean, error in zip(bars, means, errors):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + error + max(means) * 0.018, f"{mean:,.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x, [str(value) for value in repeats_list])
    ax.set_xlabel("单个 Pi 进程中的相同完整读取次数")
    ax.set_ylabel("处理的 prompt token（输入 + 缓存）")
    ax.set_title("不同长会话长度的 prompt token 流量")
    ax.legend(frameon=False, loc="upper left")
    ax.set_ylim(bottom=0)
    add_caption(ax, source_caption(report))
    fig.tight_layout()
    save(fig, "long-session-prompt-by-length.png")


def fig_savings(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 5.1))
    means: list[float] = []
    errors: list[float] = []
    for index, repeats in enumerate(repeats_list):
        seeds, baseline, dedupe = paired_rows(rows, repeats)
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
    ax.plot(np.arange(len(repeats_list)), means, color=COLORS[DEDUPE], linewidth=2, alpha=0.85, label="平均配对减少")
    ax.axhline(0, color="#777777", linewidth=1)
    ax.set_xticks(np.arange(len(repeats_list)), [str(value) for value in repeats_list])
    ax.set_xlabel("单个 Pi 进程中的相同完整读取次数")
    ax.set_ylabel("prompt token 减少比例（%）")
    ax.set_title("会话变长时观察到的节省")
    ax.set_ylim(bottom=min(0, min(means, default=0) - 10))
    ax.legend(frameon=False, loc="lower right")
    add_caption(ax, "圆点表示各个配对种子；黑点和误差线表示平均值及 95% t 区间。" + source_caption(report))
    fig.tight_layout()
    save(fig, "long-session-savings-by-length.png")


def cumulative_trajectory(rows: list[dict], arm: str, repeats: int) -> tuple[list[int], list[float]]:
    samples = []
    for row in rows_for(rows, arm, repeats):
        request_usage = row.get("requestUsage", [])
        samples.append(np.cumsum([item.get("promptTokensProcessed", 0) for item in request_usage]))
    if not samples:
        return [], []
    request_count = min(len(sample) for sample in samples)
    values = [statistics.mean(float(sample[index]) for sample in samples) for index in range(request_count)]
    return list(range(1, request_count + 1)), values


def fig_trajectory(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fig, axes = plt.subplots(1, len(repeats_list), figsize=(5.2 * len(repeats_list), 4.4), squeeze=False)
    for index, repeats in enumerate(repeats_list):
        ax = axes[0][index]
        for arm in ARM_ORDER:
            x, y = cumulative_trajectory(rows, arm, repeats)
            if x:
                ax.plot(x, y, marker="o", markersize=3.5, linewidth=2, color=COLORS[arm], label=LABELS[arm])
        ax.set_title(f"{repeats} 次读取")
        ax.set_xlabel("模型请求序号")
        if index == 0:
            ax.set_ylabel("累计 prompt token")
        ax.set_ylim(bottom=0)
        if index == len(repeats_list) - 1:
            ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.suptitle("长会话中累计 prompt token 的增长轨迹", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, textwrap.fill(source_caption(report), width=180), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "long-session-cumulative-trajectory.png")


def fig_breakdown(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fields = [
        ("inputTokens", "未缓存输入", "#426B9A"),
        ("cacheReadTokens", "缓存读取输入", "#7E9FC2"),
        ("cacheWriteTokens", "缓存写入输入", "#C1D1E1"),
    ]
    fig, axes = plt.subplots(1, len(repeats_list), figsize=(5.0 * len(repeats_list), 4.7), squeeze=False)
    for index, repeats in enumerate(repeats_list):
        ax = axes[0][index]
        x = np.arange(2)
        bottom = np.zeros(2)
        for field, label, color in fields:
            means = [
                statistics.mean([row.get(field, 0) for row in rows_for(rows, arm, repeats)])
                if rows_for(rows, arm, repeats) else 0
                for arm in ARM_ORDER
            ]
            ax.bar(x, means, bottom=bottom, color=color, width=0.58, label=label)
            bottom += np.array(means)
        ax.set_title(f"{repeats} 次读取")
        ax.set_xticks(x, ["基线", "去重"])
        ax.set_ylabel("token" if index == 0 else "")
        ax.set_ylim(bottom=0)
        if index == len(repeats_list) - 1:
            ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("长会话输入 token 的供应商缓存构成", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, textwrap.fill(source_caption(report), width=180), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "long-session-token-breakdown.png")


def fig_quality(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.8), sharey=True)
    for axis, field, title in zip(axes, ["answerCorrect", "protocolCompliant"], ["最终答案准确率", "重复读取协议遵守率"]):
        x = np.arange(len(repeats_list))
        width = 0.34
        for offset, arm in enumerate(ARM_ORDER):
            rates = []
            lower = []
            upper = []
            for repeats in repeats_list:
                sample = rows_for(rows, arm, repeats)
                successes = sum(bool(row.get(field)) for row in sample)
                rate = successes / len(sample) if sample else 0
                low, high = wilson_interval(successes, len(sample))
                rates.append(rate)
                lower.append(rate - low)
                upper.append(high - rate)
            positions = x + (offset - 0.5) * width
            bars = axis.bar(positions, rates, width=width, yerr=[lower, upper], capsize=4, color=COLORS[arm], label=LABELS[arm])
            for bar, repeats in zip(bars, repeats_list):
                sample = rows_for(rows, arm, repeats)
                successes = sum(bool(row.get(field)) for row in sample)
                axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.04, f"{successes}/{len(sample)}", ha="center", va="bottom", fontsize=8)
        axis.set_title(title)
        axis.set_xticks(x, [str(value) for value in repeats_list])
        axis.set_xlabel("读取次数")
        axis.set_ylim(0, 1.28)
        axis.legend(frameon=False, fontsize=8, loc="upper left")
    axes[0].set_ylabel("成功率")
    fig.suptitle("长会话正确性与协议检查", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, "柱高表示观测成功率；误差线表示 Wilson 95% 区间。" + source_caption(report), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "long-session-quality.png")


def fig_suppression(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.8))
    for axis, field, ylabel, title in zip(
        axes,
        ["duplicateOccurrencesSuppressed", "estimatedTokensSuppressed"],
        ["平均重复结果替换次数", "平均估算抑制 token"],
        ["插件在长会话中的累计命中", "插件诊断估算的抑制量"],
    ):
        x = np.arange(len(repeats_list))
        values = []
        errors = []
        for repeats in repeats_list:
            sample = [row.get(field, 0) for row in rows_for(rows, DEDUPE, repeats)]
            mean, ci = mean_ci([float(value) for value in sample])
            values.append(mean)
            errors.append(ci)
        bars = axis.bar(x, values, yerr=errors, capsize=5, color=COLORS[DEDUPE], width=0.58)
        for bar, value in zip(bars, values):
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values, default=1) * 0.025, f"{value:,.0f}", ha="center", va="bottom", fontsize=9)
        axis.set_title(title)
        axis.set_xticks(x, [str(value) for value in repeats_list])
        axis.set_xlabel("读取次数")
        axis.set_ylabel(ylabel)
        axis.set_ylim(bottom=0)
    fig.suptitle("重复结果命中随会话长度增长", y=1.03, fontsize=14)
    fig.text(0.01, -0.01, "估算抑制 token = 字符数 / 4，仅作诊断；账单应以供应商价格和 Pi usage 字段为准。 " + source_caption(report), fontsize=8, color="#555555")
    fig.tight_layout()
    save(fig, "long-session-suppression-metrics.png")


def format_metric(value: float) -> str:
    return f"{value:,.0f}"


def make_report(report: dict, rows: list[dict], repeats_list: list[int]) -> None:
    lines = [
        "# Pi 长会话实验验证",
        "",
        f"- 生成时间：{report.get('generatedAt', 'unknown')}",
        f"- Pi 模型：`{report.get('model', 'unknown')}`；Pi 版本：`{report.get('piVersion', 'unknown')}`；每个读取次数条件 {report.get('repetitionsPerCondition', '?')} 个种子配对。",
        f"- 任务：在同一个 Pi 进程中，连续对同一份 {report.get('fixture', {}).get('rowCount', '?')} 行文件执行多次完全相同的 `read`，最后提取随机目标 token。读取次数条件：{', '.join(str(value) for value in repeats_list)}。",
        "- 基线关闭插件；去重组加载插件。两组均使用同一模型、提示、只读工具权限和文件种子。每次模型请求都从 JSON usage 事件记录 prompt token 和 output token。",
        "",
        "## 结果摘要",
        "",
    ]

    for repeats in repeats_list:
        seeds, baseline, dedupe = paired_rows(rows, repeats)
        if not seeds:
            lines.append(f"- **{repeats} 次读取**：没有完整配对数据。")
            continue
        base_prompt = statistics.mean(baseline[seed].get("promptTokensProcessed", 0) for seed in seeds)
        dedupe_prompt = statistics.mean(dedupe[seed].get("promptTokensProcessed", 0) for seed in seeds)
        reduction = 100 * (base_prompt - dedupe_prompt) / base_prompt if base_prompt else 0
        base_correct = sum(bool(baseline[seed].get("answerCorrect")) for seed in seeds)
        dedupe_correct = sum(bool(dedupe[seed].get("answerCorrect")) for seed in seeds)
        base_protocol = sum(bool(baseline[seed].get("protocolCompliant")) for seed in seeds)
        dedupe_protocol = sum(bool(dedupe[seed].get("protocolCompliant")) for seed in seeds)
        hits = statistics.mean(dedupe[seed].get("duplicateOccurrencesSuppressed", 0) for seed in seeds)
        estimate = statistics.mean(dedupe[seed].get("estimatedTokensSuppressed", 0) for seed in seeds)
        base_requests = statistics.mean(baseline[seed].get("assistantRequests", 0) for seed in seeds)
        dedupe_requests = statistics.mean(dedupe[seed].get("assistantRequests", 0) for seed in seeds)
        lines.append(
            f"- **{repeats} 次读取（n={len(seeds)}）**：平均 prompt token 从 {format_metric(base_prompt)} 降到 {format_metric(dedupe_prompt)}，减少 **{reduction:.1f}%**；平均模型请求数为基线 {base_requests:.1f}、去重 {dedupe_requests:.1f}；准确率为基线 {base_correct}/{len(seeds)}、去重 {dedupe_correct}/{len(seeds)}；协议遵守为基线 {base_protocol}/{len(seeds)}、去重 {dedupe_protocol}/{len(seeds)}；去重组平均命中 {hits:,.0f} 次，字符估算抑制约 {estimate:,.0f} token。"
        )

    lines.extend([
        "",
        "## 图表",
        "",
        "### 不同长会话长度的 token 流量",
        "",
        "![Long-session prompt token traffic](../assets/long-session-prompt-by-length.png)",
        "",
        "### 节省比例随会话长度的变化",
        "",
        "![Long-session savings](../assets/long-session-savings-by-length.png)",
        "",
        "### 每次模型请求的累计增长轨迹",
        "",
        "![Long-session cumulative trajectory](../assets/long-session-cumulative-trajectory.png)",
        "",
        "### 输入 token 的供应商缓存构成",
        "",
        "![Long-session token breakdown](../assets/long-session-token-breakdown.png)",
        "",
        "### 正确性和协议遵守率",
        "",
        "![Long-session quality](../assets/long-session-quality.png)",
        "",
        "### 去重命中和抑制量",
        "",
        "![Long-session suppression metrics](../assets/long-session-suppression-metrics.png)",
        "",
        "## 解释",
        "",
        "- 这个实验中的长会话是同一个 Pi 进程内的多轮工具调用；`--no-session` 只是不把实验写入历史会话，不会清除本次进程内的上下文。",
        "- 随读取次数增加，基线会把越来越多的重复工具结果带入后续模型请求；去重组只保留第一次完整结果，其余相同结果变成短引用，所以两条累计曲线的斜率会逐渐分开。",
        "- 工具调用仍然执行，调用参数和 Pi 会话中的原始工具结果也仍然存在；插件只变换送给模型的请求上下文。",
        "- 在 8 次和 12 次条件中，基线分别有 0/3 次严格遵守读取次数，部分运行多读了一次或两次；这属于长会话中观察到的模型行为，因此 token 节省是端到端会话结果，也包含了这类行为差异。",
        "",
        "## 限制",
        "",
        "1. 长会话实验使用 72 行文件以避免把基线推到模型上下文上限；绝对 token 数不能直接与之前 180 行文件的短实验比较，应在同一读取次数条件内比较两组。",
        "2. 每个条件只有少量种子，准确率相同只能说明这批样本没有观察到差异，不能证明所有任务都不会受影响。",
        "3. `chars/4` 是粗略诊断值；prompt token 图使用 Pi 的 `input + cacheRead + cacheWrite`，不等于供应商账单金额。",
        "4. 读取次数和重复内容是人为构造的压力场景，真实项目中能否达到类似节省比例取决于重复工具结果的长度、重复次数和模型上下文策略。",
        "",
        "逐次 usage、答案、协议检查和每次请求的 token 轨迹见 [`long-session-results.json`](../results/long-session-results.json)。",
        "",
    ])
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "long-session-validation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    report, rows = load_results()
    repeats_list = sorted({int(row["repeats"]) for row in rows})
    if not repeats_list:
        raise SystemExit("No repeat-count conditions found in long-session results.")
    fig_prompt_by_length(report, rows, repeats_list)
    fig_savings(report, rows, repeats_list)
    fig_trajectory(report, rows, repeats_list)
    fig_breakdown(report, rows, repeats_list)
    fig_quality(report, rows, repeats_list)
    fig_suppression(report, rows, repeats_list)
    make_report(report, rows, repeats_list)
    print(f"Generated 6 long-session comparison charts and {DOCS / 'long-session-validation.md'}")


if __name__ == "__main__":
    main()
