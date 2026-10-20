import argparse
import json
from application.service import Service
from infrastructure.realtime import Archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    date = args.date
    if len(date) == 8:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    s = Service(args.data_dir)
    rows, notice = Archive(s.repo.root).replay(date, s.alert_rules)
    print(json.dumps({"notice": notice, "alerts": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
