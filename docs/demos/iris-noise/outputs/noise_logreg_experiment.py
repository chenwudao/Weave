#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Iris + LogisticRegression 训练集加性高斯噪声实验（冻结协议，真实运行）。

环境（本脚本实际执行环境；运行日志会记录实测版本）
  Python      3.12.6 (CPython, 64-bit, MSC v.1940)
  OS          Windows-11-10.0.22631-SP0
  numpy       1.26.4
  pandas      2.3.3
  scikit-learn 1.7.2
  matplotlib  3.10.3 (Agg 后端，不弹窗)
  scipy       1.14.1

运行方式（无参数、无外部数据依赖）
  python outputs/noise_logreg_experiment.py

冻结协议（不得改动）
  1. 数据：仅 sklearn.datasets.load_iris()，150 样本、4 特征、3 类；不使用其他数据集。
  2. 划分：train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
     => 120 训练 / 30 测试；所有噪声强度共用同一划分。
  3. 标准化：StandardScaler() 只用干净训练集 fit，再 transform 训练集与测试集；
     测试集永不加噪。
  4. 噪声：仅对标准化后的训练特征加性高斯噪声
     X_noisy = X_train_scaled + rng.normal(0, sigma, X_train_scaled.shape)，
     sigma ∈ {0.0, 0.1, 0.2, 0.3, 0.5}；sigma=0 也走同一代码路径（仍生成噪声向量）。
  5. 模型：LogisticRegression(solver='lbfgs', C=1.0, max_iter=1000, random_state=0)，
     每次运行重新 fit，不复用已 fit 的模型。
  6. 随机种子：sigma_index ∈ 0..4（sigma 升序），run_index ∈ 0..4，
     seed = 1000 * sigma_index + run_index，np.random.default_rng(seed)。
  7. 重复：每个 sigma 5 次，共 25 次训练/评估。
  8. 指标：干净测试集 accuracy_score（唯一主指标）；log_loss 仅内部记录，不进入主结论。
  9. 不做超参搜索、不做交叉验证、不加其他数据集或模型。

产出（写入本脚本所在目录，即 outputs/）
  results_per_run.csv   25 行逐次明细（sigma, sigma_index, run_index, seed, accuracy, log_loss, n_test）
  results_summary.csv   5 行按 sigma 汇总（均值/标准差 ddof=1/最小/最大/n）
  accuracy_vs_noise.png 5 次散点 + 均值折线 + 标准差误差棒
  run_log.txt           命令、环境版本、实际 stdout、派生读数、耗时
"""

from __future__ import annotations

import platform
import sys
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无界面后端，禁止弹窗

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

# ----------------------------------------------------------------------------
# 冻结常量（全部写死，不依赖命令行参数或环境变量）
# ----------------------------------------------------------------------------
SIGMAS = (0.0, 0.1, 0.2, 0.3, 0.5)
N_RUNS = 5
TEST_SIZE = 0.2
SPLIT_RANDOM_STATE = 42
MODEL_RANDOM_STATE = 0
SOLVER = "lbfgs"
C_VALUE = 1.0
MAX_ITER = 1000

OUT_DIR = Path(__file__).resolve().parent
PER_RUN_CSV = OUT_DIR / "results_per_run.csv"
SUMMARY_CSV = OUT_DIR / "results_summary.csv"
FIGURE_PNG = OUT_DIR / "accuracy_vs_noise.png"
RUN_LOG = OUT_DIR / "run_log.txt"

_LOG: list[str] = []


def emit(msg: str = "") -> None:
    """打印一行并逐字保留到运行日志，保证 run_log.txt 中的 stdout 是真实输出。"""
    print(msg)
    _LOG.append(msg)


def run_experiment() -> tuple[pd.DataFrame, pd.DataFrame]:
    t0 = time.perf_counter()

    emit("=" * 78)
    emit("Iris + LogisticRegression 训练集高斯噪声实验（冻结协议）")
    emit("=" * 78)
    emit(f"python           : {sys.version.splitlines()[0]}")
    emit(f"executable       : {sys.executable}")
    emit(f"platform         : {platform.platform()}")
    emit(f"numpy            : {np.__version__}")
    emit(f"pandas           : {pd.__version__}")
    emit(f"scikit-learn     : {sklearn.__version__}")
    emit(f"matplotlib       : {matplotlib.__version__}")
    emit(f"scipy            : {scipy.__version__}")
    emit(f"outputs dir      : {OUT_DIR}")
    emit("")

    # 1) 数据：仅 Iris
    iris = load_iris()
    X, y = iris.data, iris.target
    emit(f"[data] load_iris: X.shape={X.shape} y.shape={y.shape} "
         f"classes={list(iris.target_names)}")

    # 2) 划分：一次划分，所有 sigma 共用
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=SPLIT_RANDOM_STATE, stratify=y
    )
    emit(f"[split] test_size={TEST_SIZE} random_state={SPLIT_RANDOM_STATE} stratify=y "
         f"-> train={X_train.shape[0]} test={X_test.shape[0]}")
    emit(f"[split] train class counts={np.bincount(y_train).tolist()} "
         f"test class counts={np.bincount(y_test).tolist()}")

    # 3) 标准化：只在干净训练集 fit；测试集永不加噪
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    emit(f"[scaler] fit on clean train only: mean={np.round(scaler.mean_, 6).tolist()} "
         f"scale={np.round(scaler.scale_, 6).tolist()}")
    emit(f"[scaler] X_train_scaled mean={np.round(X_train_scaled.mean(axis=0), 6).tolist()}")
    emit("")

    rows: list[dict] = []
    warn_count = 0
    emit("[runs] sigma  sigma_index  run_index  seed   train_acc  test_acc   log_loss")
    for sigma_index, sigma in enumerate(SIGMAS):
        for run_index in range(N_RUNS):
            seed = 1000 * sigma_index + run_index
            rng = np.random.default_rng(seed)
            # 4) 加噪：仅在标准化后的训练特征上；sigma=0 也调用同一路径
            noise = rng.normal(0.0, sigma, X_train_scaled.shape)
            X_noisy = X_train_scaled + noise
            # 5) 每次重新 fit 一个新模型
            model = LogisticRegression(
                solver=SOLVER,
                C=C_VALUE,
                max_iter=MAX_ITER,
                random_state=MODEL_RANDOM_STATE,
            )
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                model.fit(X_noisy, y_train)
                model_warnings = [str(w.message) for w in caught]
            warn_count += len(model_warnings)

            train_acc = float(accuracy_score(y_train, model.predict(X_noisy)))
            y_pred = model.predict(X_test_scaled)          # 测试集干净
            test_acc = float(accuracy_score(y_test, y_pred))
            test_ll = float(log_loss(y_test, model.predict_proba(X_test_scaled)))

            rows.append(
                {
                    "sigma": sigma,
                    "sigma_index": sigma_index,
                    "run_index": run_index,
                    "seed": seed,
                    "accuracy": test_acc,
                    "log_loss": test_ll,
                    "n_test": int(y_test.shape[0]),
                }
            )
            emit(f"       {sigma:<6.1f} {sigma_index:^12d} {run_index:^10d} {seed:^6d} "
                 f"{train_acc:.4f}     {test_acc:.4f}    {test_ll:.6f}"
                 + ("   [warn]" if model_warnings else ""))

    per_run = pd.DataFrame(rows)

    # 汇总：ddof=1 标准差
    summary_rows = []
    for sigma in SIGMAS:
        vals = per_run.loc[per_run["sigma"] == sigma, "accuracy"].to_numpy()
        summary_rows.append(
            {
                "sigma": sigma,
                "mean_accuracy": float(vals.mean()),
                "std_accuracy": float(vals.std(ddof=1)),
                "min_accuracy": float(vals.min()),
                "max_accuracy": float(vals.max()),
                "n_runs": int(vals.size),
                "median_accuracy": float(np.median(vals)),
            }
        )
    summary = pd.DataFrame(summary_rows)

    # 派生读数（仅打印，供假设判定复核；判定不在本脚本内做）
    means = summary["mean_accuracy"].to_numpy()
    m0 = means[0]
    deltas = [float(means[i] - means[i + 1]) for i in range(len(means) - 1)]
    D = float(m0 - means[-1])
    delta_01 = float(means[1] - m0)
    S = float((m0 - means[1]) + (m0 - means[2]))
    rho = float(spearmanr(np.arange(len(SIGMAS)), means).statistic)
    emit("")
    emit("[derived] mean_accuracy by sigma: " + ", ".join(
        f"sigma={s:.1f}: {m:.6f}" for s, m in zip(SIGMAS, means)))
    emit("[derived] D = m(0) - m(0.5)                = " + f"{D:+.6f}")
    emit("[derived] delta_0.1 = m(0.1) - m(0)          = " + f"{delta_01:+.6f}")
    emit("[derived] S = (m0-m0.1)+(m0-m0.2)            = " + f"{S:+.6f}")
    emit("[derived] adjacent deltas d1..d4             = " + ", ".join(f"{d:+.6f}" for d in deltas))
    emit("[derived] spearman rho (sigma vs mean_acc)   = " + f"{rho:+.6f}")
    emit("[derived] max within-sigma SD                = "
         + f"{summary['std_accuracy'].max():.6f}")
    emit(f"[quality] convergence warnings across 25 fits = {warn_count}")

    # 保存 CSV（固定 10 位小数，便于复核重算）
    per_run.to_csv(PER_RUN_CSV, index=False, float_format="%.10f", encoding="utf-8")
    summary.to_csv(SUMMARY_CSV, index=False, float_format="%.10f", encoding="utf-8")
    emit("")
    emit(f"[write] {PER_RUN_CSV.name} rows={len(per_run)}")
    emit(f"[write] {SUMMARY_CSV.name} rows={len(summary)}")

    # 图：5 次散点 + 均值折线 + 标准差误差棒
    fig, ax = plt.subplots(figsize=(8.4, 5.2), dpi=160)
    xs = np.asarray(SIGMAS, dtype=float)
    for i, sigma in enumerate(SIGMAS):
        vals = per_run.loc[per_run["sigma"] == sigma, "accuracy"].to_numpy()
        ax.scatter(
            np.full(vals.shape, sigma), vals,
            s=44, alpha=0.75, edgecolor="black", linewidth=0.6, zorder=3,
            label="single run (5 per sigma)" if i == 0 else None,
        )
    ax.errorbar(
        xs, means, yerr=summary["std_accuracy"].to_numpy(),
        fmt="o-", color="#c0392b", linewidth=2.0, markersize=6, capsize=4,
        elinewidth=1.4, label="mean ± SD (ddof=1)", zorder=4,
    )
    for x, m in zip(xs, means):
        ax.annotate(f"{m:.3f}", (x, m), textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=8, color="#7b241c")
    ax.set_xlabel("noise sigma (Gaussian, train-only, applied after StandardScaler)")
    ax.set_ylabel("test accuracy (clean test set, n=30)")
    ax.set_title("Iris + LogisticRegression: test accuracy vs training-noise strength\n"
                 "5 runs per sigma, shared split (random_state=42)")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{s:.1f}" for s in SIGMAS])
    ax.set_ylim(0.0, 1.05)
    ax.grid(True, which="major", linestyle="--", alpha=0.4)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGURE_PNG)
    plt.close(fig)
    emit(f"[write] {FIGURE_PNG.name} ({FIGURE_PNG.stat().st_size} bytes)")

    elapsed = time.perf_counter() - t0
    emit("")
    emit(f"[done] 25 runs finished, elapsed={elapsed:.2f} s")

    # 运行日志：环境 + 命令 + 实际 stdout + 耗时
    header = [
        "command      : python outputs/noise_logreg_experiment.py",
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
        "protocol     : iris only; split test_size=0.2/random_state=42/stratify=y; "
        "StandardScaler fit on clean train only; Gaussian noise on scaled train features "
        "only (sigma in {0,0.1,0.2,0.3,0.5}); LogisticRegression lbfgs C=1.0 "
        "max_iter=1000 random_state=0 refit each run; "
        "seed=1000*sigma_index+run_index (sigma_index,run_index in 0..4); "
        "5 runs per sigma = 25; metric = clean-test accuracy",
        f"elapsed_sec  : {elapsed:.2f}",
        "",
        "----- actual stdout -----",
    ]
    RUN_LOG.write_text("\n".join(header + _LOG) + "\n", encoding="utf-8")
    return per_run, summary


if __name__ == "__main__":
    run_experiment()
