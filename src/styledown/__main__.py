import argparse
from pathlib import Path
from typing import Optional, Sequence

from .build import build_site, build_sites
from .server import run_server

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="styledown")
    parser.add_argument(
        "--input",
        default="./src/",
        help="Styledown directory to convert (default: ./src/).",
    )
    parser.add_argument(
        "--output",
        default="./dist/",
        help="Where to place the converted files (default: ./dist/).",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host interface to bind the server to (default: localhost).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1234,
        help="Port to bind the server to (default: 1234).",
    )
    parser.add_argument(
        "--domains",
        action="store_true",
        help="Serve multiple sites from subdirectories by mapping the request Host header to a subdirectory.",
    )
    args = parser.parse_args(argv)

    input_directory = Path(args.input)
    output_directory = Path(args.output)

    if not input_directory.is_dir():
        print(f" [-] Path must be a directory: {input_directory}")
        return 1

    try:
        if args.domains:
            build_sites(input_directory, output_directory)
        else:
            build_site(input_directory, output_directory)
        run_server(output_directory, host=args.host, port=args.port, domains=args.domains)
        return 0
    except Exception as e:
        print(f" [-] {e}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
