import json

from app.embed.service import reindex_source


def main() -> None:
    result = reindex_source()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
