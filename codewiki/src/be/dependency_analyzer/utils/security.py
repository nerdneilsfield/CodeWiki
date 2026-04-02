from pathlib import Path
import os


def _inside(base: Path, target: Path) -> bool:
    base_r = base.resolve()
    try:
        target_r = target.resolve()
        return target_r.is_relative_to(base_r)  # py>=3.9
    except AttributeError:
        return str(target.resolve()).startswith(str(base_r))


def assert_safe_path(base_dir: Path, target: Path):
    # Block symlinks (file or dir)
    if target.is_symlink():
        raise PermissionError(f"Symlink blocked: {target}")
    # Block paths that escape repo
    if not _inside(base_dir, target):
        raise PermissionError(f"Path escapes repo: {target} -> {target.resolve()}")


def safe_open_text(base_dir: Path, target: Path, encoding="utf-8"):
    assert_safe_path(base_dir, target)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(target), flags)
    # os.fdopen takes ownership of fd — it closes fd when the file object
    # is closed.  Do NOT call os.close(fd) again; that causes EBADF in
    # concurrent scenarios where the fd number gets reused by another thread.
    with os.fdopen(fd, "r", encoding=encoding, errors="replace") as f:
        return f.read()
