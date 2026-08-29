import csv

from django.core.management.base import BaseCommand

from equipment.models import Area, Kind, StorageLocation, Equipment


class Command(BaseCommand):
    help = "CSVファイルから機材マスタを一括インポートします"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default="data/equipment.csv",
            help="読み込むCSVファイルのパス（省略時: data/equipment.csv）",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]

        created_count = 0
        updated_count = 0
        error_rows = []

        with open(csv_path, encoding="cp932", newline="") as f:
            reader = csv.DictReader(f)

            for i, row in enumerate(reader, start=2):  # 2行目からデータ(1行目はヘッダー)
                management_code = row.get("品目コード", "").strip()
                try:
                    area, _ = Area.objects.get_or_create(
                        name=row["領域"].strip(),
                        defaults={"code": ""},
                    )

                    kind, _ = Kind.objects.get_or_create(
                        area=area,
                        name=row["種別"].strip(),
                    )

                    storage_location = None
                    storage_name = row["保管場所"].strip()
                    if storage_name:
                        storage_location, _ = StorageLocation.objects.get_or_create(
                            name=storage_name
                        )

                    equipment, created = Equipment.objects.update_or_create(
                        management_code=management_code,
                        defaults={
                            "area": area,
                            "kind": kind,
                            "model_name": row["機種"].strip(),
                            "name": row["名称"].strip(),
                            "storage_location": storage_location,
                            "notes": row["備考"].strip(),
                        },
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Exception as e:
                    error_rows.append((i, management_code, str(e)))

        self.stdout.write(self.style.SUCCESS(f"新規登録: {created_count}件"))
        self.stdout.write(self.style.SUCCESS(f"更新: {updated_count}件"))

        if error_rows:
            self.stdout.write(self.style.ERROR(f"エラー: {len(error_rows)}件"))
            for row_num, code, msg in error_rows[:20]:
                self.stdout.write(self.style.ERROR(f"  行{row_num} ({code}): {msg}"))