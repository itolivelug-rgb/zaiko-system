from collections import Counter

from django.core.management.base import BaseCommand

from equipment.models import Area, Kind


class Command(BaseCommand):
    help = "品目コードから領域コードと種別の表示順を自動設定します"

    def handle(self, *args, **options):
        # --- 領域コード（品目コードの先頭2桁） ---
        for area in Area.objects.all():
            codes = Counter(
                e.management_code[:2] for e in area.equipments.all()
            )
            if codes:
                area.code = codes.most_common(1)[0][0]
                area.save()
                self.stdout.write(f"領域 {area.name}: code = {area.code}")

        # --- 種別の表示順（品目コードの3〜4桁目） ---
        for kind in Kind.objects.select_related("area").all():
            orders = Counter(
                e.management_code[2:4] for e in kind.equipments.all()
            )
            if orders:
                kind.sort_order = int(orders.most_common(1)[0][0])
                kind.save()
                self.stdout.write(
                    f"種別 {kind.area.name} / {kind.name}: 表示順 = {kind.sort_order}"
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f"種別 {kind.area.name} / {kind.name}: 機材が0件のためスキップ")
                )

        self.stdout.write(self.style.SUCCESS("完了しました。"))