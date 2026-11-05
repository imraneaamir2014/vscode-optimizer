# VSCode Optimizer 🚀

A conservative, Windows-first utility for inspecting Visual Studio Code processes and extension sizes, and safely previewing and cleaning selected cache folders.

> **Safety first:** This tool does not delete extensions, settings, workspace storage, project files, logs, or user history. Cache cleanup is opt-in, requires VS Code to be closed, and creates a verified ZIP backup before deleting anything.

## Features

- Detects VS Code processes and reports memory usage (USS when available, otherwise RSS).
- Reports installed extension folder sizes. It does **not** claim to know which extensions are unused.
- Previews eligible cache folders and estimates their total size before cleanup.
- Cleans only: `Cache`, `CachedData`, `GPUCache`, `Code Cache`, and `DawnCache`.
- Creates and verifies a ZIP backup before cleanup.
- Skips symbolic links and Windows reparse points.
- Includes automated tests using temporary folders; tests never touch your real VS Code data.

## Screenshots

### Before
![Before](images/cache-before.png)

### After
![After](images/cache-after.png)

## Requirements

- Python 3.10+
- Windows 10/11 is the primary supported environment.
- `psutil` is needed for process/memory analysis. Cache preview and cleanup use the Python standard library.

## Install

```bash
git clone https://github.com/imrane-ai-dev/vscode-optimizer.git
cd vscode-optimizer
python -m pip install -r requirements.txt
