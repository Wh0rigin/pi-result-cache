#!/usr/bin/env python3
"""Create reproducible A/B figures and a short validation report from Pi runs."""

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
RESULTS = ROOT / "results" / "benchmark-results.json"
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"
BASELINE = "baseline"
DEDUPE = "dedupe"
ARM_ORDER = [BASELINE, DEDUPE]
COLORS = {BASELINE: "#426B9A", DEDUPE: "#2E8065"}
LABELS = {BASELINE: "Pi baseline", DEDUPE: "Result deduplication"}

plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 10,
    "figure.dpi": 140,
    "savefig.dpi": 180,
    "savefig.bbox": "tight",
})


def load_results() -> tuple[dict, list[dict]]:
    if not RESULTS.exists():
        raise SystemExit(f"Missing experiment results: {RESULTS}. Run `npm run benchmark` first.")
    report = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = report.get("results", [])
    if not rows:
        raise SystemExit("The benchmark result file has no runs to plot.")
    return report, rows


def arm_rows(rows: list[dict], arm: str) -> list[dict]:
    return [row for row in rows if row.get("arm") == arm]


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
        f"Source: Pi JSON usage events · {report.get('model', 'model unavailable')} · "
        f"{report.get('repetitionsPerArm', '?')} paired repetitions · generated {report.get('generatedAt', 'unknown')}"
    )


def add_caption(ax: plt.Axes, text: str, width: int = 105) -> None:
    wrapped = textwrap.fill(text, width=width)
    ax.text(0, -0.19, wrapped, transform=ax.transAxes, fontsize=8, color="#555555", va="top")


def save(fig: plt.Figure, filename: str) -> None:
    fig.savefig(ASSETS / filename)
    plt.close(fig)


def paired_values(rows: list[dict], metric: str) -> tuple[list[int], list[float], list[float]]:
    baseline = {row["seed"]: row for row in arm_rows(rows, BASELINE)}
    dedupe = {row["seed"]: row for row in arm_rows(rows, DEDUPE)}
    seeds = sorted(set(baseline) & set(dedupe))
    return seeds, [baseline[s].get(metric, 0) for s in seeds], [dedupe[s].get(metric, 0) for s in seeds]


def fig_input_tokens(report: dict, rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    means: list[float] = []
    errors: list[float] = []
    for arm in ARM_ORDER:
        values = [float(row.get("promptTokensProcessed", 0)) for row in arm_rows(rows, arm)]
        mean, ci = mean_ci(values)
        means.append(mean)
        errors.append(ci)
    saved_pct = 100 * (means[0] - means[1]) / means[0] if means[0] else 0
    x = np.arange(len(ARM_ORDER))
    bars = ax.bar(x, means, yerr=errors, capsize=7, color=[COLORS[a] for a in ARM_ORDER], width=0.58)
    for bar, mean, arm in zip(bars, means, ARM_ORDER):
        n = len(arm_rows(rows, arm))
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + errors[ARM_ORDER.index(arm)], f"{mean:,.0f}\nn={n}", ha="center", va="bottom")
    ax.set_xticks(x, [LABELS[a] for a in ARM_ORDER])
    ax.set_ylabel("Prompt tokens processed per run (tokens)")
    ax.set_title(f"Pi prompt-token traffic per run · {saved_pct:.1f}% lower with deduplication")
    ax.set_ylim(0, max(mean + error for mean, error in zip(means, errors)) * 1.18)
    add_caption(ax, source_caption(report))
    fig.tight_layout()
    save(fig, "input-token-comparison.png")


def fig_token_breakdown(report: dict, rows: list[dict]) -> None:
    fields = [
        ("inputTokens", "Uncached input", "#426B9A"),
        ("cacheReadTokens", "Cache-read input", "#7E9FC2"),
        ("cacheWriteTokens", "Cache-write input", "#C1D1E1"),
    ]
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    x = np.arange(len(ARM_ORDER))
    bottom = np.zeros(len(ARM_ORDER))
    for field, label, color in fields:
        means = [statistics.mean([row.get(field, 0) for row in arm_rows(rows, arm)]) if arm_rows(rows, arm) else 0 for arm in ARM_ORDER]
        ax.bar(x, means, bottom=bottom, color=color, width=0.58, label=label)
        bottom += np.array(means)
    ax.set_xticks(x, [LABELS[a] for a in ARM_ORDER])
    ax.set_ylabel("Input token count per run (tokens)")
    ax.set_title("Input-token usage split by provider cache status")
    ax.legend(frameon=False, ncols=3, loc="upper right")
    ax.set_ylim(bottom=0)
    add_caption(ax, source_caption(report))
    fig.tight_layout()
    save(fig, "input-token-breakdown.png")


def fig_accuracy(report: dict, rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    x = np.arange(len(ARM_ORDER))
    width = 0.32
    metrics = [
        ("answerCorrect", "Exact target answer", "#426B9A"),
        ("protocolCompliant", "Exactly four identical reads", "#2E8065"),
    ]
    for offset, (field, label, color) in enumerate(metrics):
        rates: list[float] = []
        lows: list[float] = []
        highs: list[float] = []
        for arm in ARM_ORDER:
            sample = arm_rows(rows, arm)
            successes = sum(bool(row.get(field)) for row in sample)
            low, high = wilson_interval(successes, len(sample))
            rates.append(successes / len(sample) if sample else 0)
            lows.append((successes / len(sample) - low) if sample else 0)
            highs.append((high - successes / len(sample)) if sample else 0)
        positions = x + (offset - 0.5) * width
        bars = ax.bar(positions, rates, width=width, yerr=[lows, highs], capsize=5, color=color, label=label)
        for bar, arm in zip(bars, ARM_ORDER):
            sample = arm_rows(rows, arm)
            successes = sum(bool(row.get(field)) for row in sample)
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.04, f"{successes}/{len(sample)}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x, [LABELS[a] for a in ARM_ORDER])
    ax.set_ylabel("Success rate (fraction of runs)")
    ax.set_title("Answer accuracy and repeated-read protocol adherence")
    ax.set_ylim(0, 1.36)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 0.99), ncols=2, fontsize=9)
    add_caption(ax, "Bars show observed rates; whiskers show Wilson 95% intervals. " + source_caption(report))
    fig.tight_layout()
    save(fig, "accuracy-and-protocol.png")


def fig_estimate_vs_observed(report: dict, rows: list[dict]) -> None:
    seeds, baseline, dedupe = paired_values(rows, "promptTokensProcessed")
    base_by_seed = {row["seed"]: row for row in arm_rows(rows, BASELINE)}
    dedupe_by_seed = {row["seed"]: row for row in arm_rows(rows, DEDUPE)}
    actual = [base_by_seed[s].get("promptTokensProcessed", 0) - dedupe_by_seed[s].get("promptTokensProcessed", 0) for s in seeds]
    estimated = [dedupe_by_seed[s].get("estimatedTokensSuppressed", 0) for s in seeds]

    fig, ax = plt.subplots(figsize=(7.2, 5.4))
    ax.scatter(estimated, actual, s=70, color=COLORS[DEDUPE], edgecolor="white", linewidth=0.8, label="Paired repetitions")
    upper = max(estimated + actual + [1]) * 1.12
    ax.plot([0, upper], [0, upper], linestyle="--", linewidth=1, color="#777777", label="Estimate = observed change")
    offsets = [(12, -18), (34, -38), (12, -58), (34, -78), (12, -98)]
    for index, (seed, x, y) in enumerate(zip(seeds, estimated, actual)):
        ax.annotate(
            f"seed {seed}",
            (x, y),
            xytext=offsets[index % len(offsets)],
            textcoords="offset points",
            fontsize=8,
            arrowprops={"arrowstyle": "-", "color": "#777777", "lw": 0.6},
        )
    ax.set_xlim(0, upper)
    ax.set_ylim(0, upper)
    ax.set_xlabel("Extension estimate of suppressed context (tokens, chars/4)")
    ax.set_ylabel("Paired reduction in Pi prompt usage (tokens)")
    ax.set_title("Estimated suppression versus observed input-token change")
    ax.legend(frameon=False)
    add_caption(ax, "Observed change = baseline minus dedupe; provider cache fields are included in prompt tokens. " + source_caption(report))
    fig.tight_layout()
    save(fig, "suppressed-vs-observed.png")


def fig_total_tokens(report: dict, rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    means: list[float] = []
    errors: list[float] = []
    for arm in ARM_ORDER:
        values = [float(row.get("totalTokensProcessed", 0)) for row in arm_rows(rows, arm)]
        mean, ci = mean_ci(values)
        means.append(mean)
        errors.append(ci)
    x = np.arange(len(ARM_ORDER))
    bars = ax.bar(x, means, yerr=errors, capsize=7, color=[COLORS[a] for a in ARM_ORDER], width=0.58)
    for bar, mean, arm in zip(bars, means, ARM_ORDER):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + errors[ARM_ORDER.index(arm)] + max(means) * 0.018,
            f"{mean:,.0f}\nn={len(arm_rows(rows, arm))}",
            ha="center",
            va="bottom",
        )
    ax.set_xticks(x, [LABELS[a] for a in ARM_ORDER])
    ax.set_ylabel("Input plus output tokens per run (tokens)")
    ax.set_title("Total model token traffic per run")
    ax.set_ylim(bottom=0)
    add_caption(ax, source_caption(report))
    fig.tight_layout()
    save(fig, "total-token-traffic.png")


def make_validation_report(report: dict, rows: list[dict]) -> None:
    base = arm_rows(rows, BASELINE)
    optimized = arm_rows(rows, DEDUPE)
    base_input = statistics.mean([r.get("promptTokensProcessed", 0) for r in base]) if base else 0
    dedupe_input = statistics.mean([r.get("promptTokensProcessed", 0) for r in optimized]) if optimized else 0
    change = 100 * (base_input - dedupe_input) / base_input if base_input else 0
    base_correct = sum(bool(r.get("answerCorrect")) for r in base)
    dedupe_correct = sum(bool(r.get("answerCorrect")) for r in optimized)
    base_protocol = sum(bool(r.get("protocolCompliant")) for r in base)
    dedupe_protocol = sum(bool(r.get("protocolCompliant")) for r in optimized)
    dedupe_suppressed = statistics.mean([r.get("estimatedTokensSuppressed", 0) for r in optimized]) if optimized else 0
    base_by_seed = {r["seed"]: r for r in base}
    dedupe_by_seed = {r["seed"]: r for r in optimized}
    paired_seeds = sorted(set(base_by_seed) & set(dedupe_by_seed))
    paired_reduction = statistics.mean([
        base_by_seed[seed].get("promptTokensProcessed", 0) - dedupe_by_seed[seed].get("promptTokensProcessed", 0)
        for seed in paired_seeds
    ]) if paired_seeds else 0

    lines = [
        "# Pi 实验验证",
        "",
        f"- 生成时间：{report.get('generatedAt', 'unknown')}",
        f"- Pi 模型：`{report.get('model', 'unknown')}`；每组 {report.get('repetitionsPerArm', '?')} 次配对重复。",
        f"- 任务：每次会话顺序读取同一份 180 行文件 4 次，再提取第 137 行的随机 token。两组使用相同的种子数据、Pi 版本、模型、提示和只读工具权限。",
        "- 对照组关闭插件；实验组加载插件。实验组只在送往模型的请求上下文中把重复结果替换为短引用，Pi 持久会话记录不被改写。",
        "",
        "## 结果",
        "",
        f"- 平均 prompt token 流量：对照组 {base_input:,.0f}，去重组 {dedupe_input:,.0f}，变化 **{change:+.1f}%**（正数表示减少）。统计按 Pi usage 的 `input + cacheRead + cacheWrite` 计算。",
        f"- 配对后每次运行平均减少 {paired_reduction:,.0f} 个 Pi prompt token；插件的 `chars/4` 诊断估算为 {dedupe_suppressed:,.0f} token/会话。估算值不代替 Pi usage 实测。",
        f"- 答案准确率：对照组 {base_correct}/{len(base)}，去重组 {dedupe_correct}/{len(optimized)}。只有最终答案与该轮文件中的随机 token 完全一致才算正确。",
        f"- 四次相同读取均按协议执行：对照组 {base_protocol}/{len(base)}，去重组 {dedupe_protocol}/{len(optimized)}。",
        "- token 用量不等于账单金额：供应商缓存读写可能按不同单价计费；本实验没有根据单价折算费用。",
        "",
        "## 图表",
        "",
        "![Pi input-token comparison](../assets/input-token-comparison.png)",
        "",
        "![Input-token cache breakdown](../assets/input-token-breakdown.png)",
        "",
        "![Answer accuracy and protocol adherence](../assets/accuracy-and-protocol.png)",
        "",
        "![Suppressed estimate versus observed reduction](../assets/suppressed-vs-observed.png)",
        "",
        "![Total model token traffic](../assets/total-token-traffic.png)",
        "",
        "## 限制",
        "",
        "1. 这是 Pi 0.87.1 上一个受控的重复读取任务，不代表真实编程任务中的平均节省比例。",
        "2. 每组重复次数有限，准确率相同只能说明当前样本未观察到差异，不能证明总体正确率完全不变。",
        "3. 文件读取仍会执行，工具调用和参数 token 仍存在；本实验测的是重复工具结果从后续模型上下文中省掉的部分。",
        "4. `chars/4` 是粗略诊断值，主要 token 结论以 Pi provider 用量事件的输入/cache 字段为准。",
        "5. A/B 顺序按种子交错；外部模型响应仍有随机性，种子是重复编号，不是 API 的随机数控制。",
        "",
        "逐次用量、答案和协议检查见 [`benchmark-results.json`](../results/benchmark-results.json)。",
        "",
    ]
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "validation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    report, rows = load_results()
    fig_input_tokens(report, rows)
    fig_token_breakdown(report, rows)
    fig_accuracy(report, rows)
    fig_estimate_vs_observed(report, rows)
    fig_total_tokens(report, rows)
    make_validation_report(report, rows)
    print(f"Generated 5 comparison charts and {DOCS / 'validation.md'}")


if __name__ == "__main__":
    main()
