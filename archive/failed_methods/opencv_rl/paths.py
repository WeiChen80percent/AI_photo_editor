from pathlib import Path


RL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = RL_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def resolve_data_dir(data_dir=None):
    if data_dir is None:
        return DEFAULT_DATA_DIR.resolve()

    path = Path(data_dir)
    candidates = [
        path,
        RL_DIR / path,
        PROJECT_ROOT / path,
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved

    # Return the project-level interpretation for clearer error messages.
    return (PROJECT_ROOT / path).resolve()


def resolve_rl_file(filename):
    path = Path(filename)
    if path.is_absolute():
        return path
    return RL_DIR / path
