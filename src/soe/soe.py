import argparse
from pathlib import Path
import logging
import pickle
import sys

from soe.fuzzer import blackbox_fuzzer, simple_fuzzer
from soe.function_list.function_list import generate_function_list
from soe.runner import dump_type_list_to_json
import soe._global as _global

logger = logging.getLogger("soe")

fuzzer_options = {
    "simple": simple_fuzzer,
    "blackbox": blackbox_fuzzer,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="soe",
        description="sturdy-octo-engine command line tool",
    )
    parser.add_argument("path", help="path of codebase")
    parser.add_argument(
        "-fl",
        "--function-list-file",
        help="provide an existing function list file (.pkl)",
        default="",
    )
    parser.add_argument(
        "-tl",
        "--type-list-file",
        help="provide an existing type list file (.pkl)",
        default="",
    )
    parser.add_argument("-o", "--output", help="specify output directory", default="output")
    parser.add_argument(
        "-f",
        "--fuzzer",
        help="specify fuzzer (simple | blackbox)",
        default="blackbox",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose")
    parser.add_argument("--no-output", action="store_true", help="disable file output")
    parser.add_argument("--no-log", action="store_true", help="disable log output")
    parser.add_argument("--no-save", action="store_true", help="disable state output")
    parser.add_argument("--no-fuzz", action="store_true", help="disable fuzzing")
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Duration for blackbox fuzzer in seconds",
    )
    parser.add_argument(
        "--time-per-func",
        type=float,
        default=1.0,
        help="Time to spend fuzzing each function in seconds (for blackbox fuzzer)",
    )
    parser.add_argument("--show-success", action="store_true", help="Log successful run details")

    args = parser.parse_args()
    soe(
        fuzz_dir=Path(args.path),
        function_list_file=Path(args.function_list_file),
        type_list_file=Path(args.type_list_file),
        output_dir=Path(args.output),
        fuzzer=args.fuzzer,
        verbose=args.verbose,
        no_log=args.no_log,
        no_save=args.no_save or args.no_output,
        no_fuzz=args.no_fuzz,
        duration=args.duration,
        time_per_func=args.time_per_func,
        show_success=args.show_success,
    )


def init_logger(level=logging.INFO, no_log=False) -> None:
    if no_log:
        logging.basicConfig(
            level=level,
            format="[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s",
            handlers=[logging.NullHandler()],
            force=True,
        )
        return

    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s",
        handlers=[
            logging.FileHandler("runtime.log", mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def soe(
    fuzz_dir: Path,
    function_list_file: Path = Path(),
    type_list_file: Path = Path(),
    output_dir: Path = Path("output"),
    fuzzer: str = "blackbox",
    verbose=False,
    no_log=False,
    no_save=False,
    no_fuzz=False,
    duration: int = 60,
    time_per_func: float = 1.0,
    show_success: bool = False,
) -> None:
    init_logger(level=logging.DEBUG if verbose else logging.INFO, no_log=no_log)
    logger.info("Starting sturdy-octo-engine on %s", fuzz_dir)

    if not fuzz_dir.exists():
        logger.critical("Repository path does not exist: %s", fuzz_dir)
        raise FileNotFoundError(f"Repository path {fuzz_dir} does not exist.")
    if not fuzz_dir.is_dir():
        logger.critical("Provided path is not a directory: %s", fuzz_dir)
        raise NotADirectoryError(f"Provided path {fuzz_dir} must be a directory.")

    _global.init_global()
    _global.set_dir_path(fuzz_dir)

    if function_list_file.is_file():
        try:
            file_size = function_list_file.stat().st_size
            if file_size == 0:
                logger.warning("Function list file is empty: %s", function_list_file)
                logger.info("Generating new function list from %s", fuzz_dir)
                function_list = generate_function_list(fuzz_dir)
                _global.set_function_list(function_list)
            else:
                with open(function_list_file, "rb") as f:
                    function_list = pickle.load(f)
                    _global.set_function_list(function_list)
                    funcs = (
                        function_list.get("functions", function_list)
                        if isinstance(function_list, dict)
                        else function_list
                    )
                    func_count = len(funcs) if isinstance(funcs, dict) else len(list(funcs))
                    logger.info(
                        "Loaded function list from %s (%s bytes, %s functions)",
                        function_list_file,
                        file_size,
                        func_count,
                    )
        except Exception as e:
            logger.warning("Failed to load function list from %s: %s", function_list_file, e)
            logger.info("Generating new function list from %s", fuzz_dir)
            function_list = generate_function_list(fuzz_dir)
            _global.set_function_list(function_list)
    else:
        logger.info("Generating new function list from %s", fuzz_dir)
        function_list = generate_function_list(fuzz_dir)
        _global.set_function_list(function_list)

    if type_list_file.is_file():
        try:
            file_size = type_list_file.stat().st_size
            if file_size == 0:
                logger.warning("Type list file is empty: %s", type_list_file)
                logger.warning("Defaulting to empty type list")
            else:
                with open(type_list_file, "rb") as f:
                    type_list = pickle.load(f)
                    _global.set_type_list(type_list)
                    logger.info("Loaded type list from %s (%s bytes)", type_list_file, file_size)
        except Exception as e:
            logger.warning("Failed to load type list from %s: %s", type_list_file, e)
            logger.warning("Defaulting to empty type list")

    if not no_fuzz:
        try:
            logger.info("Starting fuzzing")
            if fuzzer not in fuzzer_options:
                raise KeyError(f"Fuzzer '{fuzzer}' not found.")
            fuzz = fuzzer_options[fuzzer]
            if fuzzer == "blackbox":
                fuzz(
                    fuzz_dir,
                    duration_seconds=duration,
                    time_per_func=time_per_func,
                    show_success=show_success,
                )
            else:
                fuzz(fuzz_dir, show_success=show_success)
        except KeyError as e:
            logger.critical(e)
            raise
        except Exception as e:
            logger.critical("An error has occurred: %s", e)
            raise

    if not no_save:
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_dir / "function_list.pkl", "wb") as f:
            pickle.dump(_global.get_function_list(), f)
            logger.info("Saved function list to %s", output_dir / "function_list.pkl")
        with open(output_dir / "type_list.pkl", "wb") as f:
            pickle.dump(_global.get_type_list(), f)
            logger.info("Saved type list to %s", output_dir / "type_list.pkl")
        dump_type_list_to_json(_global.get_type_list(), str(output_dir / "type_list.json"))
        logger.info("Saved type list JSON to %s", output_dir / "type_list.json")

    logger.info("Exiting sturdy-octo-engine")


if __name__ == "__main__":
    main()
