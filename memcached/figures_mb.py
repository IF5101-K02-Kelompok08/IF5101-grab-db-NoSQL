import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / "benchmarks" / "published"
OUT = ROOT / "report_mc"
OUT.mkdir(exist_ok=True)

# Gambar 3.4: latensi 18 operasi
res = json.loads((PUB / "memcached" / "results.json").read_text())["results"]
ids = [r["operation_id"] for r in res]
fig, ax = plt.subplots(figsize=(9, 4.2))
ax.bar(ids, [r["mean_ms"] for r in res], color="#2b6cb0", label="rata-rata")
for i, r in enumerate(res):
    ax.scatter([i - .15, i, i + .15], r["measurements_ms"], color="black", s=10, zorder=3,
               label="run" if i == 0 else None)
ax.set_ylabel("waktu eksekusi (ms)")
ax.set_title("Latensi operasi Memcached (1 warm-up + 3 run terukur)")
ax.tick_params(axis="x", rotation=60)
ax.legend()
ax.grid(axis="y", alpha=.3)
fig.tight_layout()
fig.savefig(OUT / "fig_latency.png", dpi=200)

# Gambar 3.1: komposisi working set
load = json.loads((PUB / "memcached_load_ttl_none.json").read_text())["families"]
fam = sorted(load, key=lambda f: load[f]["bytes"], reverse=True)
fig, ax = plt.subplots(figsize=(8, 3.6))
ax.barh(fam, [load[f]["bytes"] / 1e6 for f in fam], color="#38a169")
for i, f in enumerate(fam):
    ax.text(load[f]["bytes"] / 1e6 + .3, i, f'{load[f]["keys"]:,} key'.replace(",", "."), va="center", fontsize=8)
ax.set_xlim(0, max(load[f]["bytes"] for f in fam) / 1e6 * 1.18)
ax.invert_yaxis()
ax.set_xlabel("ukuran payload (MB)")
ax.set_title("Komposisi working set Memcached per keluarga key")
ax.grid(axis="x", alpha=.3)
fig.tight_layout()
fig.savefig(OUT / "fig_keyfamilies.png", dpi=200)

# Gambar 3.2: 128 MB vs 256 MB (hanya jika percobaan 128 MB sudah dijalankan)
f128 = PUB / "memcached_validation_128m.json"
if f128.exists():
    v128 = json.loads(f128.read_text())["counts"]
    v256 = json.loads((PUB / "memcached_validation.json").read_text())["counts"]
    fams = ["order", "payment", "trip", "driver"]
    fig, ax = plt.subplots(figsize=(7, 3.4))
    x = range(len(fams))
    ax.bar([i - .2 for i in x], [100 * v128[f]["found"] / v128[f]["expected"] for f in fams], .4,
           label="-m 128 (MB)", color="#e53e3e")
    ax.bar([i + .2 for i in x], [100 * v256[f]["found"] / v256[f]["expected"] for f in fams], .4,
           label="-m 256 (MB)", color="#2b6cb0")
    ax.set_xticks(list(x), fams)
    ax.set_ylim(0, 112)
    for cont in ax.containers:
        ax.bar_label(cont, fmt="%.1f", fontsize=8)
    ax.set_ylabel("% key ditemukan")
    ax.set_title("Kelengkapan working set vs batas memori Memcached")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False)
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_memory_limit.png", dpi=200, bbox_inches="tight")
print("Grafik tersimpan di", OUT)
