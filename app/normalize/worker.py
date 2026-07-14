import json

from app.normalize.service import normalize_source


def main() -> None:
    result = normalize_source()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
