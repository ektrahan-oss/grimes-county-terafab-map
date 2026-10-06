"""One look for every analysis chart: sized for a phone, plain labels, quiet axes."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common import CHARTS  # noqa: E402

SURFACE, INK, SECONDARY, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]   # identity, in this fixed order
BLUES = {3: ["#86b6ef", "#2a78d6", "#104281"], 4: ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]}      # amount, light to dark
NEUTRAL = "#c3c2b7"
WIDTH_IN, DPI = 6, 180                         # 1080 pixels wide

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "text.color": INK,
    "axes.edgecolor": AXIS, "axes.labelcolor": SECONDARY, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "xtick.color": MUTED, "ytick.color": SECONDARY, "xtick.labelsize": 10, "ytick.labelsize": 11,
    "axes.grid": True, "axes.grid.axis": "x", "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "legend.fontsize": 10,
})


def new_chart(title, subtitle, height_in=6.5):
    """A figure with a left-aligned title and a one-line subtitle saying what is measured."""
    fig, ax = plt.subplots(figsize=(WIDTH_IN, height_in), dpi=DPI)
    fig.subplots_adjust(top=1 - 1.15 / height_in, bottom=0.95 / height_in, left=0.30, right=0.95)
    fig.text(0.04, 1 - 0.38 / height_in, title, fontsize=14, fontweight="bold", va="center")
    fig.text(0.04, 1 - 0.72 / height_in, subtitle, fontsize=10, color=SECONDARY, va="center")
    ax.tick_params(length=0)
    return fig, ax


def finish(fig, name, source):
    fig.text(0.04, 0.02, source, fontsize=8, color=MUTED, va="bottom", wrap=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    path = CHARTS / f"{name}.png"
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    print(f"Chart saved: {path.relative_to(CHARTS.parent.parent.parent).as_posix()}")
    return path
