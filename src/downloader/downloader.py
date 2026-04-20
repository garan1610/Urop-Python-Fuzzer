import argparse
from .download_repo import download_repo, download_all, list_projects
import logging
import sys

logger = logging.getLogger('downloader')


def main():
    parser = argparse.ArgumentParser(
        prog="downloader",
        description="Benchmark downloader command line tool"
    )
    parser.add_argument(
        "-i", "--install",
        help="specify project to download"
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="download all projects"
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="disable log output",
    )

    args = parser.parse_args()
    downloader(
        install=args.install if args.install else "",
        all=args.all,
        no_log=args.no_log
    )


def init_logger(level=logging.INFO, no_log=False) -> None:
    if no_log:
        logging.basicConfig(
            level=level,
            format='[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s',
            handlers=[logging.NullHandler()]
        )
        return

    logging.basicConfig(
        level=level,
        format='[%(asctime)s] [%(name)s/%(levelname)s]: %(message)s',
        handlers=[
            logging.FileHandler("runtime.log", mode="w"), 
            logging.StreamHandler(sys.stdout)
        ]
    )


def downloader(
        install: str = "",
        all: bool = False,
        no_log: bool = False
    ):
    init_logger(no_log=no_log)

    try:
        import git  # noqa: F401
    except ImportError:
        logger.error("Dependencies for downloader are not installed.")
        logger.error("Please run 'pip install -e \".[downloader]\" to install the required dependencies.")
        return

    if all:
        download_all()
    elif install:
        download_repo(install)
    else:
        list_projects()

    logger.info("Exiting downloader")


if __name__ == "__main__":
    main()
