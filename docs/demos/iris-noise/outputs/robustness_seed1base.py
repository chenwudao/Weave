#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Iris + LogisticRegression 加噪实验：1 基种子稳健性补跑（不计入主结果）。

目的：只回答"主结果的数值对种子索引基是否敏感"，不是新实验、不扩大结论。
其余口径与冻结协议逐项相同（数据集、划分、标准化、噪声位置、模型、σ 网格、重复次数、
主指标均不变），唯一改动是种子编号从 0 基改为 1 基：

    seed = 1000*(sigma_index + 1) + (run_index + 1)
    => 1001–1005, 2001–2005, 3001–3005, 4001–4005, 5001–5005
    （σ=0 组噪声恒为零，1 基与 0 基给出同一模型、同一准确率，属预期）

与主结果严格隔离：
  * 读取主结果 CSV 用于比较，不覆盖 results_per_run.csv / results_summary.csv / accuracy_vs_noise.png；
  * 只写 robustness_seed1base_* 文件；
  * 运行前后对主结果文件取 md5，证明未被改动。

产出（outputs/）
  robustness_seed1base.py            本脚本
  robustness_seed1base_per_run.csv   25 行逐次明细
  robustness_seed1base_summary.csv   5 行汇总
  robustness_seed1base_compare.csv   0 基 vs 1 基并排对比（5 行）
  robustness_seed1base.png           同规格曲线图
  robustness_seed1base_log.txt       命令、环境、实测 stdout、派生读数、判定

判定读法（沿用工程台已发布的假设判定卡，未改阈值）：
  H1：成立 D≥0.02 且 ρ≤−0.9 且各组 SD<0.05；反驳 D≤0.01 或 ρ≥−0.5 或 (D<0 且 |D|≥0.02)；
      其余记待定。
  H2：以 |Δ0.1| ≤ 0.02 为等价界（等号计入），且 5 次逐次差值不构成稳定单向大偏移
      （5 个差值同号且每项 |d|≥2 个测试样本）→ 支持；|Δ0.1| 落在 (0.02, 0.03] → 待定；
      |Δ0.1| > 0.03 → 超出等价界。方向（升/降）单独报。
      注：H2 假设卡的字面措辞把「Δ0.1 ≥ 0.02」写成"低强度明显有害"，与 Δ0.1 的定义方向相反；
      本脚本不改写阈值，只按判定卡已采用的 |Δ0.1| 读法执行，并在日志中标注该措辞问题。
  H3：成立需 D0.5≥0.03 且 S≤0.02 且 D0.5−S≥0.02 且 Δ4≥2·max(Δ1,Δ2)；
      反驳 S≥0.02 或 D0.5<0.03 或 Δ1..Δ4 极差<0.01；其余记待定。

用法： python outputs/robustness_seed1base.py
"""

from __future__ import annotations

import hashlib
import platform
import sys
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
import sklearn
from scipy.stats import spearmanr
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

SIGMAS = (0.0, 0.1, 0.2, 0.3, 0.5)
N_RUNS = 5
TEST_SIZE = 0.2
SPLIT_RANDOM_STATE = 42
MODEL_RANDOM_STATE = 0
SOLVER = "lbfgs"
C_VALUE = 1.0
MAX_ITER = 1000

OUT = Path(__file__).resolve().parent
PER_RUN_CSV = OUT / "robustness_seed1base_per_run.csv"
SUMMARY_CSV = OUT / "robustness_seed1base_summary.csv"
COMPARE_CSV = OUT / "robustness_seed1base_compare.csv"
FIGURE_PNG = OUT / "robustness_seed1base.png"
RUN_LOG = OUT / "robustness_seed1base_log.txt"

MAIN_FILES = (
    OUT / "results_per_run.csv",
    OUT / "results_summary.csv",
    OUT / "accuracy_vs_noise.png",
)

_L: list[str] = []


def emit(msg: str = "") -> None:
    print(msg)
    _L.append(msg)


def md5(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def derive(means: np.ndarray, stds: np.ndarray, k_mat: np.ndarray) -> dict:
    m0, m1, m2, m3, m5 = (float(v) for v in means)
    d = m0 - m5
    d01 = m1 - m0
    s_ = (m0 - m1) + (m0 - m2)
    rho = float(spearmanr(np.arange(len(SIGMAS)), means).statistic)
    deltas = [float(means[i] - means[i + 1]) for i in range(len(means) - 1)]
    per_run = [int(v) for v in (k_mat[1] - k_mat[0])]
    return {
        "means": means,
        "stds": stds,
        "D": d,
        "delta01": d01,
        "S": s_,
        "rho": rho,
        "adjacent": deltas,
        "per_run_delta_01": per_run,
    }


def verdicts(dv: dict) -> dict:
    means, stds = dv["means"], dv["stds"]
    d, d01, s_ = dv["D"], dv["delta01"], dv["S"]
    rho = dv["rho"]
    adj = dv["adjacent"]
    per_run = dv["per_run_delta_01"]
    out = {}

    if d >= 0.02 and rho <= -0.9 and float(np.max(stds)) < 0.05:
        h1 = "支持"
    elif d <= 0.01 or rho >= -0.5 or (d < 0 and abs(d) >= 0.02):
        h1 = "反驳"
    else:
        h1 = "待定"
    out["H1"] = h1

    stable = all(x >= 2 for x in per_run) or all(x <= -2 for x in per_run)
    a01 = abs(d01)
    if a01 <= 0.02 + 1e-12 and not stable:
        h2 = "支持" if a01 < 0.02 - 1e-12 else "支持（边界，等号）"
    elif a01 <= 0.03:
        h2 = "待定"
    else:
        h2 = "超出等价界"
    out["H2"] = h2
    out["H2_stable_offset"] = stable

    d05 = means[0] - means[4]
    shape_ok = adj[3] >= 2 * max(adj[0], adj[1])
    spread = max(adj) - min(adj)
    if d05 >= 0.03 and s_ <= 0.02 and (d05 - s_) >= 0.02 and shape_ok:
        h3 = "支持"
    elif s_ >= 0.02 or d05 < 0.03 or spread < 0.01:
        h3 = "反驳"
    else:
        h3 = "待定"
    out["H3"] = h3
    out["shape_ok"] = bool(shape_ok)
    out["spread"] = float(spread)
    return out


def run() -> int:
    t0 = time.perf_counter()
    emit("=" * 78)
    emit("稳健性补跑：1 基种子（seed = 1000*(σ序号+1) + (重复序号+1)）")
    emit("=" * 78)
    emit(f"python           : {sys.version.splitlines()[0]}")
    emit(f"executable       : {sys.executable}")
    emit(f"platform         : {platform.platform()}")
    emit(f"numpy            : {np.__version__}")
    emit(f"pandas           : {pd.__version__}")
    emit(f"scikit-learn     : {sklearn.__version__}")
    emit(f"matplotlib       : {matplotlib.__version__}")
    emit(f"scipy            : {scipy.__version__}")
    emit(f"outputs dir      : {OUT}")
    emit("")
    emit("[isolation] 主结果文件运行前 md5：")
    before = {p.name: md5(p) for p in MAIN_FILES if p.exists()}
    for name, h in before.items():
        emit(f"[isolation]   {name} = {h}")
    emit("")

    iris = load_iris()
    X, y = iris.data, iris.target
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=SPLIT_RANDOM_STATE, stratify=y
    )
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    emit(f"[data] load_iris: X.shape={X.shape} classes={list(iris.target_names)}")
    emit(f"[split] test_size={TEST_SIZE} random_state={SPLIT_RANDOM_STATE} stratify=y "
         f"-> train={X_train.shape[0]} test={X_test.shape[0]} "
         f"(class counts {np.bincount(y_train).tolist()} / {np.bincount(y_test).tolist()})")
    emit(f"[scaler] fit on clean train only: "
         f"mean={np.round(scaler.mean_, 6).tolist()} scale={np.round(scaler.scale_, 6).tolist()}")
    emit("")

    rows: list[dict] = []
    warn_count = 0
    emit("[runs] sigma  sigma_index  run_index  seed   train_acc  test_acc   log_loss")
    for si, sigma in enumerate(SIGMAS):
        for ri in range(N_RUNS):
            seed = 1000 * (si + 1) + (ri + 1)          # 1 基口径
            rng = np.random.default_rng(seed)
            noise = rng.normal(0.0, sigma, X_train_scaled.shape)
            X_noisy = X_train_scaled + noise
            model = LogisticRegression(
                solver=SOLVER, C=C_VALUE, max_iter=MAX_ITER,
                random_state=MODEL_RANDOM_STATE,
            )
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model.fit(X_noisy, y_train)
                ws = [str(w.message) for w in caught]
            warn_count += len(ws)
            train_acc = float(accuracy_score(y_train, model.predict(X_noisy)))
            test_acc = float(accuracy_score(y_test, model.predict(X_test_scaled)))
            test_ll = float(log_loss(y_test, model.predict_proba(X_test_scaled)))
            rows.append({
                "sigma": sigma, "sigma_index": si, "run_index": ri, "seed": seed,
                "accuracy": test_acc, "log_loss": test_ll, "n_test": int(y_test.shape[0]),
            })
            emit(f"       {sigma:<6.1f} {si:^12d} {ri:^10d} {seed:^6d} "
                 f"{train_acc:.4f}     {test_acc:.4f}    {test_ll:.6f}"
                 + ("   [warn]" if ws else ""))

    per_run = pd.DataFrame(rows)
    summary_rows = []
    for sigma in SIGMAS:
        vals = per_run.loc[per_run["sigma"] == sigma, "accuracy"].to_numpy()
        summary_rows.append({
            "sigma": sigma,
            "mean_accuracy": float(vals.mean()),
            "std_accuracy": float(vals.std(ddof=1)),
            "min_accuracy": float(vals.min()),
            "max_accuracy": float(vals.max()),
            "n_runs": int(vals.size),
            "median_accuracy": float(np.median(vals)),
        })
    summary = pd.DataFrame(summary_rows)

    per_run.to_csv(PER_RUN_CSV, index=False, float_format="%.10f", encoding="utf-8")
    summary.to_csv(SUMMARY_CSV, index=False, float_format="%.10f", encoding="utf-8")
    emit("")
    emit(f"[write] {PER_RUN_CSV.name} rows={len(per_run)}")
    emit(f"[write] {SUMMARY_CSV.name} rows={len(summary)}")

    means = summary["mean_accuracy"].to_numpy()
    stds = summary["std_accuracy"].to_numpy()
    k_mat = np.rint(per_run["accuracy"].to_numpy() * 30).astype(int).reshape(len(SIGMAS), N_RUNS)
    dv1 = derive(means, stds, k_mat)
    v1 = verdicts(dv1)

    emit("")
    emit("[derived] 1 基：mean_accuracy by sigma: "
         + ", ".join(f"sigma={s:.1f}: {m:.6f}" for s, m in zip(SIGMAS, means)))
    emit(f"[derived] 1 基：D = m(0)-m(0.5)      = {dv1['D']:+.6f}")
    emit(f"[derived] 1 基：Δ0.1 = m(0.1)-m(0)   = {dv1['delta01']:+.6f}")
    emit(f"[derived] 1 基：S = (m0-m0.1)+(m0-m0.2) = {dv1['S']:+.6f}")
    emit(f"[derived] 1 基：相邻边际 Δ1..Δ4      = "
         + ", ".join(f"{d:+.6f}" for d in dv1["adjacent"]))
    emit(f"[derived] 1 基：Spearman rho        = {dv1['rho']:+.6f}")
    emit(f"[derived] 1 基：各组 SD              = "
         + ", ".join(f"{v:.6f}" for v in stds))
    emit(f"[derived] 1 基：H2 逐次差值(样本数)  = {dv1['per_run_delta_01']}")
    emit(f"[derived] 1 基：正确数 k             = {k_mat.tolist()}")
    emit(f"[quality] 1 基：收敛警告数           = {warn_count}")

    # ---- 与 0 基主结果并排对比 ----
    emit("")
    emit("=" * 78)
    emit("[compare] 0 基（主结果） vs 1 基（稳健性补跑）")
    main_summary = OUT / "results_summary.csv"
    if not main_summary.exists():
        emit("[compare] 缺少 results_summary.csv，无法对比")
        return 1
    m_sum = pd.read_csv(main_summary)
    m_means = m_sum["mean_accuracy"].to_numpy()
    m_stds = m_sum["std_accuracy"].to_numpy()
    m_pr = pd.read_csv(OUT / "results_per_run.csv")
    m_k = np.rint(m_pr["accuracy"].to_numpy() * 30).astype(int).reshape(len(SIGMAS), N_RUNS)
    dv0 = derive(m_means, m_stds, m_k)
    v0 = verdicts(dv0)

    emit(f"[compare] {'sigma':>6} {'0基均值':>10} {'1基均值':>10} {'差(1-0)':>10} "
         f"{'0基SD':>9} {'1基SD':>9} {'0基k':>18} {'1基k':>18}")
    for i, sigma in enumerate(SIGMAS):
        emit(f"[compare] {sigma:6.1f} {m_means[i]:10.6f} {means[i]:10.6f} "
             f"{means[i] - m_means[i]:+10.6f} {m_stds[i]:9.6f} {stds[i]:9.6f} "
             f"{str(m_k[i].tolist()):>18} {str(k_mat[i].tolist()):>18}")
    emit("")
    for tag, dv in (("0 基", dv0), ("1 基", dv1)):
        emit(f"[compare] {tag}：D={dv['D']:+.6f}  Δ0.1={dv['delta01']:+.6f}  "
             f"S={dv['S']:+.6f}  rho={dv['rho']:+.6f}")
    emit("")
    emit("[compare] 假设判定（同一预注册阈值，仅换种子基）：")
    emit(f"[compare]   H1：0 基={v0['H1']}  1 基={v1['H1']}  -> "
         f"{'一致' if v0['H1'] == v1['H1'] else '不一致'}")
    emit(f"[compare]   H2：0 基={v0['H2']}  1 基={v1['H2']}  -> "
         f"{'一致' if v0['H2'] == v1['H2'] else '不一致'}")
    emit(f"[compare]     逐次差值(样本数)：0 基={dv0['per_run_delta_01']}  "
         f"1 基={dv1['per_run_delta_01']}")
    emit(f"[compare]   H3：0 基={v0['H3']}  1 基={v1['H3']}  -> "
         f"{'一致' if v0['H3'] == v1['H3'] else '不一致'}")
    emit(f"[compare]   形状条件 Δ4>=2·max(Δ1,Δ2)：0 基={v0['shape_ok']}  1 基={v1['shape_ok']}")
    emit(f"[compare] 均值最大偏移 = "
         f"{float(np.max(np.abs(means - m_means))):.6f}，"
         f"端点差变化 = {dv1['D'] - dv0['D']:+.6f}，"
         f"Δ0.1 变化 = {dv1['delta01'] - dv0['delta01']:+.6f}")

    cmp_rows = []
    for i, sigma in enumerate(SIGMAS):
        cmp_rows.append({
            "sigma": sigma,
            "mean_0based": float(m_means[i]),
            "mean_1based": float(means[i]),
            "diff_1minus0": float(means[i] - m_means[i]),
            "std_0based": float(m_stds[i]),
            "std_1based": float(stds[i]),
            "k_0based": str(m_k[i].tolist()),
            "k_1based": str(k_mat[i].tolist()),
        })
    pd.DataFrame(cmp_rows).to_csv(COMPARE_CSV, index=False, float_format="%.10f", encoding="utf-8")
    emit(f"[write] {COMPARE_CSV.name} rows={len(cmp_rows)}")

    # ---- 图 ----
    fig, ax = plt.subplots(figsize=(8.4, 5.2), dpi=160)
    xs = np.asarray(SIGMAS, dtype=float)
    for i, sigma in enumerate(SIGMAS):
        vals = per_run.loc[per_run["sigma"] == sigma, "accuracy"].to_numpy()
        ax.scatter(np.full(vals.shape, sigma), vals, s=44, alpha=0.75,
                   edgecolor="black", linewidth=0.6, zorder=3,
                   label="single run (5 per sigma)" if i == 0 else None)
    ax.errorbar(xs, means, yerr=stds, fmt="o-", color="#c0392b", linewidth=2.0,
                markersize=6, capsize=4, elinewidth=1.4,
                label="1-based seeds: mean ± SD (ddof=1)", zorder=4)
    for x_, m_ in zip(xs, means):
        ax.annotate(f"{m_:.3f}", (x_, m_), textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=8, color="#7b241c")
    ax.set_xlabel("noise sigma (Gaussian, train-only, applied after StandardScaler)")
    ax.set_ylabel("test accuracy (clean test set, n=30)")
    ax.set_title("Iris + LogisticRegression: robustness check with 1-based seeds\n"
                 "seed = 1000*(sigma_index+1)+(run_index+1) -> 1001-1005 ... 5001-5005")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{s:.1f}" for s in SIGMAS])
    ax.set_ylim(0.0, 1.05)
    ax.grid(True, which="major", linestyle="--", alpha=0.4)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGURE_PNG)
    plt.close(fig)
    emit(f"[write] {FIGURE_PNG.name} ({FIGURE_PNG.stat().st_size} bytes)")

    # ---- 隔离校验 ----
    emit("")
    after = {p.name: md5(p) for p in MAIN_FILES if p.exists()}
    unchanged = all(before.get(k) == v for k, v in after.items()) and set(before) == set(after)
    emit("[isolation] 主结果文件运行后 md5：")
    for name, h in after.items():
        emit(f"[isolation]   {name} = {h}")
    emit(f"[isolation] 主结果未被改动：{unchanged}")

    elapsed = time.perf_counter() - t0
    emit("")
    emit(f"[done] 25 runs finished, elapsed={elapsed:.2f} s")
    emit("[note] 本批 1 基结果只是种子敏感性检查，不并入主结果、不扩大结论；"
         "正式结果仍为 0 基那批 25 次运行。")

    header = [
        "command      : python outputs/robustness_seed1base.py",
        f"cwd          : {Path.cwd()}",
        f"started      : {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"python       : {sys.version.splitlines()[0]}",
        f"executable   : {sys.executable}",
        f"platform     : {platform.platform()}",
        f"numpy        : {np.__version__}",
        f"pandas       : {pd.__version__}",
        f"scikit-learn : {sklearn.__version__}",
        f"matplotlib   : {matplotlib.__version__}",
        f"scipy        : {scipy.__version__}",
        "protocol     : identical to the frozen protocol except seed base; "
        "iris only; split test_size=0.2/random_state=42/stratify=y; StandardScaler fit on "
        "clean train only; Gaussian noise on scaled train features only "
        "(sigma in {0,0.1,0.2,0.3,0.5}); LogisticRegression lbfgs C=1.0 max_iter=1000 "
        "random_state=0 refit each run; seed=1000*(sigma_index+1)+(run_index+1) "
        "(sigma_index,run_index in 0..4) -> 1001-1005,2001-2005,3001-3005,4001-4005,5001-5005; "
        "5 runs per sigma = 25; metric = clean-test accuracy",
        "isolation    : writes only robustness_seed1base_* files; main result files "
        "md5-checked before and after, never rewritten",
        f"elapsed_sec  : {elapsed:.2f}",
        "",
        "----- actual stdout -----",
    ]
    RUN_LOG.write_text("\n".join(header + _L) + "\n", encoding="utf-8")
    return 0 if unchanged else 2


if __name__ == "__main__":
    raise SystemExit(run())
