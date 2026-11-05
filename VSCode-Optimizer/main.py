"""Command-line interface for VSCode Optimizer."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from optimizer import VSCodeOptimizer

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely inspect VS Code memory/extensions and preview cache cleanup."
    )
    parser.add_argument("--user-data-dir", type=Path, help="Custom VS Code user-data directory.")
    parser.add_argument("--extensions-dir", type=Path, help="Custom VS Code extensions directory.")
    parser.add_argument("--backup-dir", type=Path, help="Where verified cache backups are stored.")
    parser.add_argument("--processes", action="store_true", help="Show detected VS Code processes.")
    parser.add_argument("--extensions", action="store_true", help="Show the largest installed extensions.")
    parser.add_argument("--clean", action="store_true", help="Clean the approved cache folders after confirmation.")
    parser.add_argument("--yes", action="store_true", help="Confirm cleanup (use only after reviewing the preview).")
    return parser

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    app = VSCodeOptimizer(args.user_data_dir, args.extensions_dir, args.backup_dir)

    if args.processes:
        processes = app.get_vscode_processes()
        if not processes:
            print("No VS Code processes detected (or process details are unavailable).")
        for proc in processes:
            print(f"PID {proc.pid:<7} {proc.memory_mb:>8.2f} MB {proc.memory_metric:<3}  {proc.name}")

    if args.extensions:
        extensions = app.analyze_extensions()
        if not extensions:
            print("No extensions found, or the extensions directory is unavailable.")
        for ext in extensions:
            print(f"{ext.size_mb:>8.2f} MB  {ext.name}")

    if args.clean:
        preview = app.preview_clean()
        print(f"Cache folders: {len(preview['targets'])}")
        print(f"Files found:   {preview['files_found']}")
        print(f"Estimated:     {preview['size_mb']:.2f} MB")
        if not args.yes:
            print("Preview only. To proceed, close VS Code and run again with --clean --yes.")
            return 0
        try:
            result = app.clean_cache(confirm=True)
        except (RuntimeError, ValueError, OSError) as exc:
            print(f"Cleanup cancelled: {exc}", file=sys.stderr)
            return 2
        print(f"Backup:        {result.backup_path or 'not needed'}")
        print(f"Removed files: {result.files_removed}")
        print(f"Removed size:  {result.bytes_removed / (1024 * 1024):.2f} MB (estimated file sizes)")
        if result.errors:
            print(f"Warnings:      {len(result.errors)} (see output below)")
            for error in result.errors:
                print(f" - {error}")
        print("Cleanup finished.")
    if not (args.processes or args.extensions or args.clean):
        print("Choose an action: --processes, --extensions, or --clean.")
        print("Tip: run `python main.py --clean` first for a preview.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
