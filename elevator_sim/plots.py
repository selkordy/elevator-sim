"""Charts for a single run and for the experiments (requires matplotlib)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Charts need matplotlib: pip install matplotlib") from exc

from .simulation import SimResult

# Categorical slots in fixed order; colour follows the entity, never its rank.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SCHEDULER_COLORS = {
    "destination_dispatch": SERIES[0],
    "nearest_car": SERIES[1],
    "zone": SERIES[2],
    "round_robin": SERIES[3],
}
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def _style(ax, title: str = "", xlabel: str = "", ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="semibold")
    if xlabel:
        ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK_2, fontsize=9)


def _figure(*args, **kwargs):
    fig, axes = plt.subplots(*args, **kwargs)
    fig.patch.set_facecolor(SURFACE)
    return fig, axes


def _legend(ax, **kwargs) -> None:
    leg = ax.legend(frameon=False, fontsize=9, labelcolor=INK_2, **kwargs)
    return leg


def scheduler_color(name: str) -> str:
    return SCHEDULER_COLORS.get(name.split("(")[0], SERIES[4])


# -------------------------------------------------------------- single run


def plot_run(result: SimResult, out: Path) -> List[Path]:
    out.mkdir(parents=True, exist_ok=True)
    return [_plot_trace(result, out / "elevator_trace.png"), _plot_times(result, out / "time_distributions.png")]


def _plot_trace(result: SimResult, path: Path) -> Path:
    cfg = result.config
    fig, (ax, ax2) = _figure(
        2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ts = range(result.ticks)
    for i in range(cfg.num_elevators):
        floors = [row[i] for row in result.positions]
        label = f"car {i}" + (" (express)" if cfg.served_set(i) is not None else "")
        ax.plot(ts, floors, color=SERIES[i % len(SERIES)], linewidth=1.4, label=label)
    _style(ax, f"Car positions over time - {result.scheduler}", ylabel="floor")
    ax.set_ylim(cfg.lowest_floor - 0.5, cfg.highest_floor + 0.5)
    _legend(ax, loc="upper left", bbox_to_anchor=(1.0, 1.0))

    ax2.fill_between(ts, result.waiting, step="post", color=SERIES[0], alpha=0.25, linewidth=0)
    ax2.plot(ts, result.waiting, drawstyle="steps-post", color=SERIES[0], linewidth=1.2)
    _style(ax2, "Passengers waiting for pickup", xlabel="time (ticks)", ylabel="waiting")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _plot_times(result: SimResult, path: Path) -> Path:
    done = [p for p in result.passengers if p.dropoff_time is not None]
    fig, axes = _figure(1, 2, figsize=(11, 3.8))
    for ax, key, title in (
        (axes[0], "wait_time", "Wait time"),
        (axes[1], "total_time", "Total time (wait + ride)"),
    ):
        values = [getattr(p, key) for p in done]
        if values:
            bins = min(40, max(5, max(values) - min(values) + 1))
            ax.hist(values, bins=bins, color=SERIES[0], edgecolor=SURFACE, linewidth=1.0)
            mean = sum(values) / len(values)
            ax.axvline(mean, color=INK, linewidth=1.2, linestyle="--")
            ax.annotate(
                f"mean {mean:.1f}",
                xy=(mean, 1),
                xycoords=("data", "axes fraction"),
                xytext=(4, -12),
                textcoords="offset points",
                color=INK,
                fontsize=9,
            )
        _style(ax, title, xlabel="ticks", ylabel="passengers")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ------------------------------------------------------------ experiments


def plot_scheduler_comparison(rows: List[Dict], path: Path, metric: str = "total_mean") -> Path:
    patterns = list(dict.fromkeys(r["pattern"] for r in rows))
    schedulers = list(dict.fromkeys(r["scheduler"] for r in rows))
    fig, ax = _figure(figsize=(10, 4.2))
    width = 0.8 / len(schedulers)
    for j, s in enumerate(schedulers):
        xs, ys, p95 = [], [], []
        for i, pat in enumerate(patterns):
            r = next(r for r in rows if r["pattern"] == pat and r["scheduler"] == s)
            xs.append(i - 0.4 + width * (j + 0.5))
            ys.append(r[metric])
            p95.append(r["total_p95"])
        ax.bar(xs, ys, width=width * 0.9, color=scheduler_color(s), label=s, edgecolor=SURFACE)
        ax.scatter(xs, p95, marker="_", s=120, color=INK, linewidths=1.6, zorder=3)
    ax.set_xticks(range(len(patterns)))
    ax.set_xticklabels(patterns)
    _style(ax, "Mean total time per passenger by traffic pattern (bars: mean, ticks: p95)", ylabel="ticks")
    _legend(ax, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_load_sweep(rows: List[Dict], path: Path) -> Path:
    patterns = list(dict.fromkeys(r["pattern"] for r in rows))
    schedulers = list(dict.fromkeys(r["scheduler"] for r in rows))
    fig, axes = _figure(1, len(patterns), figsize=(5.2 * len(patterns), 4), sharey=True)
    if len(patterns) == 1:
        axes = [axes]
    for ax, pat in zip(axes, patterns):
        for s in schedulers:
            pts = sorted((r["rate"], r["total_mean"]) for r in rows if r["pattern"] == pat and r["scheduler"] == s)
            ax.plot(
                [p[0] for p in pts],
                [p[1] for p in pts],
                color=scheduler_color(s),
                linewidth=2,
                marker="o",
                markersize=5,
                label=s,
            )
        _style(ax, f"{pat}: mean total time vs load", xlabel="arrivals per tick", ylabel="ticks")
    _legend(axes[-1], loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_fairness(rows: List[Dict], path: Path) -> Path:
    """Efficiency (mean) against fairness (p99): bottom-left is best."""
    patterns = list(dict.fromkeys(r["pattern"] for r in rows))
    fig, axes = _figure(1, len(patterns), figsize=(5.2 * len(patterns), 4.2))
    if len(patterns) == 1:
        axes = [axes]
    styles = {
        "DD p=1": ("destination_dispatch", "o", "none"),
        "DD p=2": ("destination_dispatch", "o", None),
        "zone": ("zone", "s", None),
        "nearest car": ("nearest_car", "D", None),
    }
    for ax, pat in zip(axes, patterns):
        for r in rows:
            if r["pattern"] != pat or r["label"] not in styles:
                continue
            sched, marker, face = styles[r["label"]]
            color = scheduler_color(sched)
            ax.scatter(
                r["total_mean"],
                r["total_p99"],
                s=70,
                marker=marker,
                facecolors=face or color,
                edgecolors=color,
                linewidths=1.8,
                label=r["label"],
                zorder=3,
            )
        sub = [r for r in rows if r["pattern"] == pat and r["label"] in styles]
        ax.set_xlim(0, max(r["total_mean"] for r in sub) * 1.15)
        ax.set_ylim(0, max(r["total_p99"] for r in sub) * 1.15)
        _style(ax, pat, xlabel="mean total time (ticks)", ylabel="p99 total time (ticks)")
        ax.grid(axis="x", color=GRID, linewidth=0.8)
    _legend(axes[-1], loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.suptitle(
        "Efficiency vs fairness near saturation (bottom-left is better on both)",
        x=0.01,
        ha="left",
        color=INK,
        fontsize=11,
        fontweight="semibold",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_express(rows: List[Dict], path: Path) -> Path:
    patterns = list(dict.fromkeys(r["pattern"] for r in rows))
    layouts = list(dict.fromkeys(r["layout"] for r in rows))
    fig, ax = _figure(figsize=(9, 4))
    width = 0.8 / len(layouts)
    for j, lay in enumerate(layouts):
        xs, ys = [], []
        for i, pat in enumerate(patterns):
            r = next(r for r in rows if r["pattern"] == pat and r["layout"] == lay)
            xs.append(i - 0.4 + width * (j + 0.5))
            ys.append(r["total_mean"])
        ax.bar(xs, ys, width=width * 0.9, color=SERIES[j], label=lay, edgecolor=SURFACE)
    ax.set_xticks(range(len(patterns)))
    ax.set_xticklabels(patterns)
    _style(ax, "Mean total time by fleet layout (60 floors, 6 cars, no door time)", ylabel="ticks")
    _legend(ax, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_metric_bars(labels: Sequence[str], values: Sequence[float], path: Path, title: str) -> Path:
    fig, ax = _figure(figsize=(7, 3.5))
    ax.bar(range(len(values)), values, color=SERIES[0], edgecolor=SURFACE)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    _style(ax, title, ylabel="ticks")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
