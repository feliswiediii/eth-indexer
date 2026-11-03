"""Single image, two modes:
    python -m ethidx indexer
    python -m ethidx api
"""

import logging
import sys

from .config import Config


def main() -> None:
    cfg = Config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full URLs including API keys
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "indexer":
        from .indexer import main as run_indexer
        run_indexer(cfg)
    elif mode == "api":
        import uvicorn
        uvicorn.run("ethidx.api:app", host="0.0.0.0", port=cfg.api_port,
                    proxy_headers=True, log_level=cfg.log_level.lower())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
