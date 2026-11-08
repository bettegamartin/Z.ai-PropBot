"""Generate publication charts for the PDF report (charts.md compliant:
no top/right spines, dashed grid 20%, frameless legends, first/last/max/min
labels, cascade palette only, horizontal bars for long category names)."""
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Noto Sans SC']
plt.rcParams['axes.unicode_minus'] = False

DATA = "/home/z/my-project/download/data"
OUT = DATA

# Cascade palette (from palette.cascade, seed 11)
ACCENT = '#87702b'
ACCENT_2 = '#7962bd'
HEADER_FILL = '#6f6340'
ICON = '#9c8646'
BORDER = '#d2cdc0'
TEXT_PRIMARY = '#1d1d1b'
TEXT_MUTED = '#8d8a83'

def style_ax(ax, grid_axis='y'):
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(BORDER)
    ax.spines['bottom'].set_color(BORDER)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)
    if grid_axis:
        ax.grid(True, axis=grid_axis, linestyle='--', alpha=0.2, linewidth=0.5)
        ax.set_axisbelow(True)

# ---------------- Chart 1: equity curve ----------------
daily = pd.read_csv(f"{DATA}/daily_pnl.csv")
dp = daily[(daily["strat"] == "VWAPFD") & (daily["variant"] == 1)]
piv = dp.pivot_table(index="date", columns="sym", values="net", aggfunc="sum").fillna(0.0)
piv["tot"] = piv["ES"] + piv["NQ"]
eq = piv["tot"].cumsum().values
x = pd.to_datetime(piv.index)

fig, ax = plt.subplots(figsize=(8.2, 3.4), dpi=150, constrained_layout=True)
ax.plot(x, eq, color=ACCENT, linewidth=2.2, solid_capstyle='round')
ax.fill_between(x, eq, eq.min() * 0.0, color=ACCENT, alpha=0.12, linewidth=0)
# first / last / max / min labels
pts = {"Start": 0, "End": len(eq) - 1, "Max": int(np.argmax(eq)), "Min": int(np.argmin(eq))}
for name, i in pts.items():
    ax.scatter(x[i], eq[i], s=18, color=HEADER_FILL, zorder=5)
    dy = 14 if name != "Min" else -20
    ax.annotate(f"{name} ${eq[i]:,.0f}", (x[i], eq[i]), textcoords="offset points",
                xytext=(4, dy), fontsize=8.2, color=TEXT_PRIMARY)
ax.axhline(0, color=BORDER, linewidth=0.8)
style_ax(ax)
# thin x ticks: at most ~6 labels
import matplotlib.dates as mdates
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=5))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.set_ylabel("Cumulative P&L per micro pair (USD)", fontsize=9, color=TEXT_PRIMARY)
fig.savefig(f"{OUT}/chart_equity.png")
plt.close(fig)

# ---------------- Chart 2: strategy comparison (OOS expectancy) ----------------
trades = pd.read_csv(f"{DATA}/trades.csv")
oos = trades[trades["split"] == "OOS"]
rows = []
for (s, v, y), g in oos.groupby(["strat", "variant", "sym"]):
    rows.append((f"{s} v{v}", y, g["net_usd"].mean()))
df = pd.DataFrame(rows, columns=["strat", "sym", "exp"])
df = df.sort_values("exp", ascending=True)
labels = [f"{r.strat} - {r.sym}" for r in df.itertuples()]
colors_map = {"ES": HEADER_FILL, "NQ": ICON}
cols = [colors_map[s] for s in df["sym"]]

fig, ax = plt.subplots(figsize=(8.2, 3.9), dpi=150, constrained_layout=True)
bars = ax.barh(labels, df["exp"], color=cols, height=0.62, edgecolor='none')
for b, val in zip(bars, df["exp"]):
    off = 0.6 if val >= 0 else -0.6
    ax.text(val + off, b.get_y() + b.get_height() / 2, f"${val:,.2f}",
            va='center', ha='left' if val >= 0 else 'right', fontsize=8.2, color=TEXT_PRIMARY)
ax.axvline(0, color=BORDER, linewidth=0.8)
style_ax(ax, grid_axis=None)
ax.set_xlabel("Net expectancy per trade, USD (out-of-sample, 1 micro)", fontsize=9, color=TEXT_PRIMARY)
import matplotlib.patches as mpatches
leg = ax.legend(handles=[mpatches.Patch(color=HEADER_FILL, label='ES (executed as MES)'),
                         mpatches.Patch(color=ICON, label='NQ (executed as MNQ)')],
                loc='lower right', frameon=False, fontsize=8.5)
fig.savefig(f"{OUT}/chart_strategies.png")
plt.close(fig)

# ---------------- Chart 3: firm comparison (base scenario) ----------------
simres = json.load(open(f"{DATA}/sim_results.json"))["base"]
firms = [r["firm"] for r in simres]
days = [r["exp_days_to_cash"] for r in simres]
net = [r["exp_net_cash"] for r in simres]
order = np.argsort(days)
firms_o = [firms[i] for i in order]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.6, 3.6), dpi=150, constrained_layout=True)
ypos = np.arange(len(firms_o))
net_o = [net[i] for i in order]
ax1.barh(ypos, [days[i] for i in order], color=HEADER_FILL, height=0.62, edgecolor='none')
for y, v in zip(ypos, [days[i] for i in order]):
    ax1.text(v + 2, y, f"{v:.0f}d", va='center', fontsize=8.2, color=TEXT_PRIMARY)
ax1.set_yticks(ypos, firms_o, fontsize=8)
ax1.invert_yaxis()
style_ax(ax1, grid_axis=None)
ax1.set_xlabel("Expected days to first payout (lower is better)", fontsize=8.6, color=TEXT_PRIMARY)

order2 = np.argsort(net)
ax2.barh(np.arange(len(firms_o)), [net[i] for i in order2],
         color=[ACCENT if net[i] >= 0 else '#8f4e48' for i in order2], height=0.62, edgecolor='none')
for y, v in zip(np.arange(len(firms_o)), [net[i] for i in order2]):
    ax2.text(v + (30 if v >= 0 else -30), y, f"${v:,.0f}", va='center',
             ha='left' if v >= 0 else 'right', fontsize=8.2, color=TEXT_PRIMARY)
ax2.set_yticks(np.arange(len(firms_o)), [firms[i] for i in order2], fontsize=8)
ax2.invert_yaxis()
ax2.axvline(0, color=BORDER, linewidth=0.8)
style_ax(ax2, grid_axis=None)
ax2.set_xlabel("Expected NET cash at first payout (higher is better)", fontsize=8.6, color=TEXT_PRIMARY)
fig.savefig(f"{OUT}/chart_firms.png")
plt.close(fig)

# ---------------- Chart 4: MC day distributions ----------------
mc = json.load(open(f"{DATA}/mc_samples.json"))
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.6, 3.2), dpi=150, constrained_layout=True)
bins = np.linspace(30, 220, 40)
for name, col in [("Apex 50K", ICON), ("Topstep 50K", HEADER_FILL), ("MFFU RapidEOD 50K", ACCENT_2)]:
    ax1.hist(mc[name]["days"], bins=bins, alpha=0.55, color=col, label=name, edgecolor='none')
ax1.axvline(90, color=TEXT_MUTED, linewidth=0.8, linestyle='--')
ax1.text(92, ax1.get_ylim()[1] * 0.9, "3 months", fontsize=8, color=TEXT_MUTED)
style_ax(ax1)
ax1.set_xlabel("Calendar days to first payout", fontsize=8.6, color=TEXT_PRIMARY)
ax1.set_ylabel("MC trials", fontsize=8.6, color=TEXT_PRIMARY)
ax1.legend(frameon=False, fontsize=7.8, loc='upper right')

bins2 = np.linspace(0, 3500, 36)
for name, col in [("Apex 50K", ICON), ("Topstep 50K", HEADER_FILL), ("MFFU RapidEOD 50K", ACCENT_2)]:
    ax2.hist(mc[name]["cash"], bins=bins2, alpha=0.55, color=col, label=name, edgecolor='none')
style_ax(ax2)
ax2.set_xlabel("First payout received, USD (90-100% split applied)", fontsize=8.6, color=TEXT_PRIMARY)
fig.savefig(f"{OUT}/chart_dist.png")
plt.close(fig)

print("charts saved: chart_equity, chart_strategies, chart_firms, chart_dist")
