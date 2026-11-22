"""Figure 05 for the report: mean execution time vs rows CouchDB sends to the client.

The rows per operation are counted from the canonical dataset (view_rows.py), because the
benchmark run on the Mac predates the notebook's own couch_rows counter.
"""
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

evidence = Path(sys.argv[1])
view_rows = json.loads(Path(sys.argv[2]).read_text())
out = evidence / 'figures' / '05_baris_dikirim_couchdb.png'

INK, INK2, MUTED = '#0b0b0b', '#52514e', '#898781'
GRID, AXIS, SURFACE = '#e1e0d9', '#c3c2b7', '#fcfcfb'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a']
TARGET_MS = {'Operasional': 100, 'Transaksional': 200, 'Analitik': 2000}
CATEGORY_COLOR = dict(zip(TARGET_MS, SERIES))
plt.rcParams.update({
    'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
    'font.family': 'sans-serif', 'font.sans-serif': ['Helvetica Neue', 'Arial', 'DejaVu Sans'],
    'font.size': 10, 'axes.titlesize': 12, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left',
    'axes.edgecolor': AXIS, 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': INK2,
    'axes.spines.top': False, 'axes.spines.right': False, 'grid.color': GRID,
    'grid.linewidth': 0.6, 'legend.frameon': False,
})

timing = pd.read_csv(evidence / 'analysis_timing.csv').fillna({'part2': ''})
timing['baris_dikirim'] = timing['operation_id'].map(lambda o: view_rows.get(o, {}).get('view_rows'))
reads = timing.dropna(subset=['baris_dikirim'])

fig, ax = plt.subplots(figsize=(10, 5.6))
for category in ('Operasional', 'Analitik'):
    part = reads[reads['kategori'] == category]
    ax.scatter(part['baris_dikirim'], part['rata2_ms'], s=64, color=CATEGORY_COLOR[category],
               edgecolor=SURFACE, linewidth=1.5, zorder=3, label=category)
for category in ('Operasional', 'Analitik'):
    target = TARGET_MS[category]
    ax.axhline(target, color=INK2, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(0.75, target * 1.12, f'target {category.lower()} ≤ {target:,} ms', color=INK2, fontsize=8.5)
labels = {'PF-08-Q01': (8, 2), 'PF-08-Q03': (-10, -1), 'PF-08-Q02': (-10, -3), 'PF-02-Q04': (8, -3),
          'PF-10-Q03': (-10, -3), 'PF-09-Q02': (8, -3), 'PF-10-Q02': (-10, -3), 'PF-05-Q01': (8, -3),
          'PF-09-Q01': (8, -8), 'PF-10-Q01': (8, 2)}
for _, row in reads.iterrows():
    if row['operation_id'] in labels:
        dx, dy = labels[row['operation_id']]
        mark = '✓' if row['lulus_target'] else '✗'
        ax.annotate(f"{mark} {row['part2'] or row['operation_id']}  {row['rata2_ms']:,.0f} ms",
                    (row['baris_dikirim'], row['rata2_ms']), textcoords='offset points',
                    xytext=(dx, dy), ha='left' if dx > 0 else 'right', fontsize=8.5, color=INK2)
ax.set_xscale('log'); ax.set_yscale('log')
ax.set_xlim(0.5, 1.2e6); ax.set_ylim(1, 40000)
for scale in (ax.xaxis, ax.yaxis):
    scale.set_major_locator(LogLocator(base=10, subs=(1,)))
    scale.set_major_formatter(FuncFormatter(lambda v, _: f'{v:,.0f}' if v >= 1 else f'{v:g}'))
    scale.set_minor_formatter(NullFormatter())
ax.grid(axis='both'); ax.set_axisbelow(True)
ax.set_xlabel('baris view / dokumen yang dikirim CouchDB ke client per eksekusi (skala log)')
ax.set_ylabel('rata-rata waktu eksekusi (ms, skala log)')
ax.set_title('Waktu baca naik mengikuti jumlah baris yang harus dikirim CouchDB', pad=26)
ax.legend(handles=[Line2D([], [], marker='o', linestyle='', color=CATEGORY_COLOR[c], markersize=8,
                          label=c) for c in ('Operasional', 'Analitik')]
          + [Line2D([], [], color=INK2, linewidth=1, linestyle=(0, (4, 3)), label='target Tabel 3.9')],
          loc='lower left', bbox_to_anchor=(0, 1.0), ncol=3, fontsize=9)
fig.text(0, -0.02, 'Baris dihitung dari dataset canonical (view_rows.py); waktu dari runs.csv '
         '(3 run terukur setelah 1 warm-up). Operasi tulis tidak ditampilkan.', fontsize=8, color=MUTED)
fig.tight_layout()
fig.savefig(out, dpi=200, bbox_inches='tight')

x, y = np.log10(reads['baris_dikirim']), np.log10(reads['rata2_ms'])
print(out, 'pearson r (log-log) =', round(float(np.corrcoef(x, y)[0, 1]), 3))
print(reads[['operation_id', 'part2', 'baris_dikirim', 'rata2_ms', 'lulus_target']].sort_values('baris_dikirim').to_string(index=False))
