import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from model_paths import CURRENT_HISTORY_DIR
from paths import RL_DIR


STAGE_LABELS = {
    "root": "training",
    "stage1_v3_like": "stage 1: v3-like bootstrap",
    "stage2_v5_finetune": "stage 2: v5 fine-tune",
    "stage1_reference_teacher": "stage 1: reference teacher PPO",
    "stage4_student_ppo": "stage 4: no-reference student PPO",
    "teacher": "reference teacher validation",
    "student": "no-reference student validation",
}
STAGE_COLORS = {
    "root": "#1f77b4",
    "stage1_v3_like": "#1f77b4",
    "stage2_v5_finetune": "#2ca02c",
    "stage1_reference_teacher": "#1f77b4",
    "stage4_student_ppo": "#2ca02c",
    "teacher": "#1f77b4",
    "student": "#2ca02c",
}


def stage_sort_key(stage_name):
    if stage_name == "root":
        return (0, stage_name)
    if stage_name.startswith("stage1"):
        return (1, stage_name)
    if stage_name.startswith("stage2"):
        return (2, stage_name)
    if stage_name.startswith("stage3"):
        return (3, stage_name)
    if stage_name.startswith("stage4"):
        return (4, stage_name)
    return (9, stage_name)


def read_monitor_file(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        if not first_line.startswith("#"):
            f.seek(0)

        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append(
                    {
                        "reward": float(row["r"]),
                        "length": int(float(row["l"])),
                        "time": float(row["t"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def monitor_files(path):
    return sorted(path.glob("*.monitor.csv"))


def monitor_start_time(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            first_line = file.readline().strip()
        if not first_line.startswith("#"):
            return None
        metadata = json.loads(first_line[1:])
        return float(metadata["t_start"])
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def discover_log_groups(log_dir, include_root=False):
    stage_dirs = sorted(
        [path for path in log_dir.iterdir() if path.is_dir() and path.name.startswith("stage")],
        key=lambda path: stage_sort_key(path.name),
    )
    staged_groups = [
        (stage_dir.name, monitor_files(stage_dir))
        for stage_dir in stage_dirs
        if monitor_files(stage_dir)
    ]

    root_files = monitor_files(log_dir)
    groups = []
    if root_files and (include_root or not staged_groups):
        groups.append(("root", root_files))
    groups.extend(staged_groups)

    if groups:
        return groups

    recursive_files = sorted(log_dir.rglob("*.monitor.csv"))
    grouped = {}
    for file_path in recursive_files:
        stage_name = file_path.parent.relative_to(log_dir).as_posix()
        grouped.setdefault(stage_name, []).append(file_path)
    return sorted(grouped.items(), key=lambda item: stage_sort_key(item[0]))


def filter_groups_for_current_run(log_dir, groups, validation_records):
    manifest_path = log_dir / "run_manifest.json"
    run_started_at = None
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as file:
                run_started_at = float(json.load(file)["started_at"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            run_started_at = None

    if run_started_at is not None:
        filtered = []
        ignored = []
        for stage_name, files in groups:
            current_files = [
                path
                for path in files
                if (
                    monitor_start_time(path) is not None
                    and monitor_start_time(path) >= run_started_at - 5.0
                )
            ]
            if current_files:
                filtered.append((stage_name, current_files))
            else:
                ignored.append(stage_name)
        return filtered, ignored

    validation_stages = {
        record.get("stage")
        for record in validation_records
    }
    stage_mapping = {
        "teacher": "stage1_reference_teacher",
        "student": "stage4_student_ppo",
    }
    allowed_stages = {
        stage_mapping[stage]
        for stage in validation_stages
        if stage in stage_mapping
    }
    if allowed_stages:
        filtered = [
            group
            for group in groups
            if group[0] in allowed_stages
        ]
        ignored = [
            stage_name
            for stage_name, _ in groups
            if stage_name not in allowed_stages
        ]
        return filtered, ignored

    validation_path = log_dir / "validation_history.jsonl"
    if validation_path.exists():
        stage_starts = {
            stage_name: max(
                (
                    start_time
                    for start_time in (
                        monitor_start_time(path)
                        for path in files
                    )
                    if start_time is not None
                ),
                default=None,
            )
            for stage_name, files in groups
        }
        newest_start = max(
            (
                start_time
                for start_time in stage_starts.values()
                if start_time is not None
            ),
            default=None,
        )
        if newest_start is not None:
            filtered = [
                group
                for group in groups
                if (
                    stage_starts[group[0]] is not None
                    and stage_starts[group[0]] >= newest_start - 300.0
                )
            ]
            ignored = [
                stage_name
                for stage_name, _ in groups
                if stage_name not in {name for name, _ in filtered}
            ]
            return filtered, ignored

    return groups, []


def load_grouped_results(groups):
    all_x = []
    all_y = []
    all_stage = []
    stage_boundaries = []
    offset = 0

    for stage_name, files in groups:
        rows = []
        for file_path in files:
            rows.extend(read_monitor_file(file_path))
        rows.sort(key=lambda row: row["time"])

        if not rows:
            continue

        lengths = np.array([row["length"] for row in rows], dtype=np.int64)
        rewards = np.array([row["reward"] for row in rows], dtype=np.float32)
        timesteps = np.cumsum(lengths) + offset

        all_x.extend(timesteps.tolist())
        all_y.extend(rewards.tolist())
        all_stage.extend([stage_name] * len(rewards))

        offset = int(timesteps[-1])
        stage_boundaries.append((offset, stage_name))

    return (
        np.array(all_x, dtype=np.int64),
        np.array(all_y, dtype=np.float32),
        np.array(all_stage, dtype=object),
        stage_boundaries[:-1],
    )


def moving_average(values, window_size):
    if window_size <= 1:
        return values.copy()
    return np.convolve(values, np.ones(window_size) / window_size, mode="valid")


def read_validation_history(log_dir):
    path = log_dir / "validation_history.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "objective" in record and "model_timesteps" in record:
                records.append(record)
    return records


def read_bc_validation_history(log_dir):
    path = log_dir / "bc_validation_history.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "epoch" in record and "objective" in record:
                records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--include-root",
        action="store_true",
        help="Include root-level monitor logs even when staged logs are present.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=None,
        help="Moving-average window. Defaults to 10 percent of episodes, capped at 100.",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir) if args.log_dir else CURRENT_HISTORY_DIR
    if not log_dir.exists():
        print(f"No history directory found: {log_dir}")
        return

    groups = discover_log_groups(log_dir, include_root=args.include_root)
    if not groups:
        print(f"No monitor logs found in {log_dir}")
        return

    validation_records = read_validation_history(log_dir)
    bc_records = read_bc_validation_history(log_dir)
    groups, ignored_groups = filter_groups_for_current_run(
        log_dir,
        groups,
        validation_records,
    )
    if ignored_groups:
        print(
            "Ignored stale/inactive monitor groups: "
            + ", ".join(sorted(ignored_groups))
        )
    if not groups:
        print(f"No monitor logs belong to the active run in {log_dir}")
        return

    x, y, stages, boundaries = load_grouped_results(groups)
    if len(y) == 0:
        print(f"No monitor rows found in {log_dir}")
        return

    window_size = args.window if args.window else max(min(100, len(y) // 10), 1)
    window_size = max(min(window_size, len(y)), 1)

    teacher_records = [
        record
        for record in validation_records
        if record["stage"] == "teacher"
    ]
    student_records = [
        record
        for record in validation_records
        if record["stage"] == "student"
    ]

    panel_names = []
    if teacher_records:
        panel_names.append("teacher")
    if bc_records:
        panel_names.append("bc")
    if student_records:
        panel_names.append("student")
    if panel_names:
        panel_count = len(panel_names)
        fig = plt.figure(figsize=(max(7 * panel_count, 13), 10))
        grid = fig.add_gridspec(
            2,
            panel_count,
            height_ratios=(1.35, 1.0),
        )
        reward_ax = fig.add_subplot(grid[0, :])
        panel_axes = {
            name: fig.add_subplot(grid[1, index])
            for index, name in enumerate(panel_names)
        }
        teacher_validation_ax = panel_axes.get("teacher")
        bc_validation_ax = panel_axes.get("bc")
        student_validation_ax = panel_axes.get("student")
    else:
        fig, reward_ax = plt.subplots(1, 1, figsize=(11, 6))
        teacher_validation_ax = None
        bc_validation_ax = None
        student_validation_ax = None

    for stage_name in sorted(set(stages), key=stage_sort_key):
        mask = stages == stage_name
        label = STAGE_LABELS.get(stage_name, stage_name)
        color = STAGE_COLORS.get(stage_name, "#9467bd")
        reward_ax.scatter(
            x[mask],
            y[mask],
            alpha=0.22,
            color=color,
            s=2,
            label=label,
        )
        stage_x = x[mask]
        stage_y = y[mask]
        stage_window = min(window_size, len(stage_y))
        stage_smoothed = moving_average(stage_y, stage_window)
        reward_ax.plot(
            stage_x[stage_window - 1 :],
            stage_smoothed,
            color=color,
            linewidth=2.5,
            label=f"{label} SMA {stage_window}",
        )

        summary_count = max(len(stage_y) // 10, 1)
        print(
            f"{label}: episodes={len(stage_y)}, "
            f"first 10% mean={np.mean(stage_y[:summary_count]):.4f}, "
            f"last 10% mean={np.mean(stage_y[-summary_count:]):.4f}"
        )

    for boundary_x, stage_name in boundaries:
        reward_ax.axvline(
            boundary_x,
            color="#444444",
            linestyle="--",
            linewidth=1.0,
            alpha=0.65,
        )
        reward_ax.text(
            boundary_x,
            float(np.max(y)),
            "next RL stage",
            rotation=90,
            va="top",
            ha="right",
            fontsize=9,
            color="#444444",
        )

    reward_ax.set_title("Training Reward Convergence", fontsize=14)
    reward_ax.set_xlabel("Training Timesteps", fontsize=12)
    reward_ax.set_ylabel("Episode Reward", fontsize=12)
    reward_ax.grid(True, linestyle="--", alpha=0.7)
    reward_ax.legend()

    if (
        teacher_validation_ax is not None
        or bc_validation_ax is not None
        or student_validation_ax is not None
    ):
        if teacher_records:
            teacher_x = [record["model_timesteps"] for record in teacher_records]
            teacher_y = [record["objective"] for record in teacher_records]
            teacher_best = int(np.argmin(teacher_y))
            teacher_validation_ax.plot(
                teacher_x,
                teacher_y,
                marker="o",
                color=STAGE_COLORS["teacher"],
                linewidth=2.0,
                label="teacher objective",
            )
            teacher_validation_ax.scatter(
                [teacher_x[teacher_best]],
                [teacher_y[teacher_best]],
                color=STAGE_COLORS["teacher"],
                edgecolors="black",
                s=80,
                zorder=3,
                label="best checkpoint",
            )
            teacher_validation_ax.set_title(
                "Teacher Validation (has expert reference)",
                fontsize=12,
            )
            if len(teacher_records) == 1:
                teacher_validation_ax.text(
                    0.02,
                    0.08,
                    "Only one validation checkpoint is available; "
                    "the line appears after the next evaluation.",
                    transform=teacher_validation_ax.transAxes,
                    fontsize=9,
                    color="#555555",
                )
            teacher_validation_ax.legend()

        if bc_validation_ax is not None:
            bc_x = [record["epoch"] for record in bc_records]
            bc_objective = [record["objective"] for record in bc_records]
            bc_edit_mae = [record["edit_mae"] for record in bc_records]
            bc_gate_mae = [record["gate_mae"] for record in bc_records]
            bc_best = int(np.argmin(bc_objective))
            bc_validation_ax.plot(
                bc_x,
                bc_objective,
                marker="o",
                color="#9467bd",
                linewidth=2.0,
                label="BC validation objective",
            )
            bc_validation_ax.plot(
                bc_x,
                bc_edit_mae,
                marker="s",
                color="#8c564b",
                linewidth=1.5,
                label="slider MAE",
            )
            bc_validation_ax.plot(
                bc_x,
                bc_gate_mae,
                marker="^",
                color="#e377c2",
                linewidth=1.5,
                label="gate MAE",
            )
            bc_validation_ax.scatter(
                [bc_x[bc_best]],
                [bc_objective[bc_best]],
                color="#9467bd",
                edgecolors="black",
                s=80,
                zorder=3,
                label="best BC epoch",
            )
            bc_rate_axis = bc_validation_ax.twinx()
            bc_rate_axis.plot(
                bc_x,
                [
                    record["active_sign_accuracy"]
                    for record in bc_records
                ],
                color="#2ca02c",
                linestyle="--",
                label="active sign accuracy",
            )
            bc_rate_axis.plot(
                bc_x,
                [record["mean_cosine"] for record in bc_records],
                color="#1f77b4",
                linestyle="--",
                label="direction cosine",
            )
            bc_rate_axis.set_ylim(0.0, 1.0)
            bc_rate_axis.set_ylabel("Direction quality", fontsize=10)
            bc_validation_ax.set_title(
                "Behavior Cloning Validation (held out)",
                fontsize=12,
            )
            bc_lines, bc_labels = (
                bc_validation_ax.get_legend_handles_labels()
            )
            rate_lines, rate_labels = (
                bc_rate_axis.get_legend_handles_labels()
            )
            bc_validation_ax.legend(
                bc_lines + rate_lines,
                bc_labels + rate_labels,
                fontsize=8,
            )

        if student_records:
            student_x = [record["model_timesteps"] for record in student_records]
            student_objective = [record["objective"] for record in student_records]
            student_style = [
                record.get("mean_final_style_score", np.nan)
                for record in student_records
            ]
            raw_style = student_records[0].get("mean_raw_style_score")
            student_best = int(np.argmin(student_objective))
            student_validation_ax.plot(
                student_x,
                student_objective,
                marker="o",
                color=STAGE_COLORS["student"],
                linewidth=2.0,
                label="selection objective",
            )
            student_validation_ax.plot(
                student_x,
                student_style,
                marker="s",
                color="#d95f02",
                linewidth=1.8,
                label="edited style score",
            )
            if raw_style is not None:
                student_validation_ax.axhline(
                    raw_style,
                    color="#555555",
                    linestyle="--",
                    linewidth=1.6,
                    label="raw-image style score",
                )
            student_validation_ax.scatter(
                [student_x[student_best]],
                [student_objective[student_best]],
                color=STAGE_COLORS["student"],
                edgecolors="black",
                s=80,
                zorder=3,
                label="best checkpoint",
            )
            rate_axis = None
            if any(
                "close_quartile_regression_rate" in record
                for record in student_records
            ):
                rate_axis = student_validation_ax.twinx()
                close_regression = [
                    record.get("close_quartile_regression_rate", np.nan)
                    for record in student_records
                ]
                far_improvement = [
                    record.get("far_quartile_improvement_rate", np.nan)
                    for record in student_records
                ]
                rate_axis.plot(
                    student_x,
                    close_regression,
                    color="#c44e52",
                    linestyle="--",
                    linewidth=1.5,
                    label="close-image regression rate",
                )
                rate_axis.plot(
                    student_x,
                    far_improvement,
                    color="#4c956c",
                    linestyle="--",
                    linewidth=1.5,
                    label="far-image improvement rate",
                )
                rate_axis.set_ylim(0.0, 1.0)
                rate_axis.set_ylabel("Image rate", fontsize=10)
            student_validation_ax.set_title(
                "Student Validation (no expert input)",
                fontsize=12,
            )
            if len(student_records) == 1:
                student_validation_ax.text(
                    0.02,
                    0.08,
                    "Only one student checkpoint is available; "
                    "the line appears after the next evaluation.",
                    transform=student_validation_ax.transAxes,
                    fontsize=9,
                    color="#555555",
                )
            score_lines, score_labels = (
                student_validation_ax.get_legend_handles_labels()
            )
            if rate_axis is not None:
                rate_lines, rate_labels = rate_axis.get_legend_handles_labels()
                score_lines += rate_lines
                score_labels += rate_labels
            student_validation_ax.legend(score_lines, score_labels, fontsize=8)
        for validation_ax in (
            axis
            for axis in (
                teacher_validation_ax,
                bc_validation_ax,
                student_validation_ax,
            )
            if axis is not None
        ):
            validation_ax.set_xlabel("Stage Timesteps", fontsize=11)
            validation_ax.set_ylabel("Score (lower is better)", fontsize=11)
            validation_ax.grid(True, linestyle="--", alpha=0.7)
        if bc_validation_ax is not None:
            bc_validation_ax.set_xlabel("BC Epoch", fontsize=11)

    fig.tight_layout()

    output_path = Path(args.output) if args.output else RL_DIR / f"{log_dir.name}_convergence_plot.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Loaded monitor groups: {', '.join(name for name, _ in groups)}")
    print(f"Saved plot: {output_path}")


if __name__ == "__main__":
    main()
