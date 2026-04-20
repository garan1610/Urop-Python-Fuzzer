# sturdy-octo-engine

## Installation

```batch
pip install -e ".[downloader]"
```

## Download Commands

```batch
downloader
downloader -i <project_name>
downloader -a
```

## Running Commands

```batch
soe <project_path>

- Skip fuzzing and only rebuild the function list:
soe <project_path> --no-fuzz


- Simple fuzzer:
soe <project_path> -f simple


- Black box:
soe <project_path> -f blackbox --duration 60 --time-per-func 1.0 --show-success


- Reuse old metadata:
soe <project_path> --function-list-file output/function_list.pkl --type-list-file output/type_list.pkl


- Options:
-v, --verbose
Turn on debug-level logging.

--no-output
Disable saving output files.
This now behaves like --no-save.

--no-log
Disable log output.

--no-save
Do not write function_list.pkl, type_list.pkl, or type_list.json.

--no-fuzz
Only scan/build metadata; do not actually fuzz.

--duration
Only used by blackbox.
Total fuzzing time in seconds.
Default: 60

--time-per-func
Only used by blackbox.
Time budget per function in seconds.
Default: 1.0

--show-success
Log successful runs too, not just errors/skips.

```
