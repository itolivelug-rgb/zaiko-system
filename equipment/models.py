from functools import cached_property

from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

class Area(models.Model):
    """領域（PA / Light / Visual / Other）"""
    code = models.CharField(max_length=2, unique=True, verbose_name="コード")
    name = models.CharField(max_length=20, unique=True, verbose_name="領域名")

    class Meta:
        verbose_name = "領域"
        verbose_name_plural = "領域"
        ordering = ["code"]

    def __str__(self):
        return self.name


class Kind(models.Model):
    """種別（領域に紐づく中分類。例：Speaker, Mic/DIなど）"""
    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        related_name="kinds",
        verbose_name="領域",
    )
    name = models.CharField(max_length=50, verbose_name="種別名")
    sort_order = models.IntegerField(default=0, verbose_name="表示順")

    class Meta:
        verbose_name = "種別"
        verbose_name_plural = "種別"
        unique_together = ("area", "name")
        ordering = ["area__code", "sort_order", "name"]

    def __str__(self):
        return f"{self.area.name} / {self.name}"


class StorageLocation(models.Model):
    """保管場所（例：1st, 2nd など。今後追加・変更される可能性があるためマスタ化）"""
    name = models.CharField(max_length=50, unique=True, verbose_name="保管場所名")

    class Meta:
        verbose_name = "保管場所"
        verbose_name_plural = "保管場所"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(models.Model):
    """機材マスタ"""

    management_code = models.CharField(
        max_length=10, unique=True, verbose_name="品目コード"
    )
    area = models.ForeignKey(
        Area, on_delete=models.PROTECT, related_name="equipments", verbose_name="領域"
    )
    kind = models.ForeignKey(
        Kind, on_delete=models.PROTECT, related_name="equipments", verbose_name="種別"
    )
    model_name = models.CharField(max_length=200, verbose_name="機種")
    name = models.CharField(max_length=200, blank=True, verbose_name="名称")
    storage_location = models.ForeignKey(
        StorageLocation,
        on_delete=models.PROTECT,
        related_name="equipments",
        null=True,
        blank=True,
        verbose_name="保管場所",
    )
    notes = models.TextField(blank=True, verbose_name="備考")
    is_deleted = models.BooleanField(default=False, verbose_name="削除済み")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="登録日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        verbose_name = "機材"
        verbose_name_plural = "機材"
        ordering = ["management_code"]

    def __str__(self):
        return self.model_name
    @property
    def current_assignment(self):
        """現在貸出中の割り当て（出庫済みかつ未返却）"""
        return self.assignments.filter(
            checked_out_at__isnull=False, returned_at__isnull=True
        ).select_related("project").first()

    @property
    def active_assignments(self):
        """未返却の割り当てすべて（予約中・貸出中を含む）"""
        return self.assignments.filter(
            returned_at__isnull=True, project__is_deleted=False
        ).select_related("project")

    @property
    def loan_status_label(self):
        """貸出状況（貸出中 / 準備中 / 予約なし）"""
        if self.current_assignment:
            return "貸出中"
        if self.active_assignments.exists():
            return "準備中"
        return "予約なし"

    @property
    def project_names(self):
        """関係する案件名（複数ある場合はカンマ区切り）"""
        return "、".join(a.project.name for a in self.active_assignments)

class EquipmentHistory(models.Model):
    """機材の変更履歴"""

    ACTION_CHOICES = [
        ("create", "登録"),
        ("update", "変更"),
        ("delete", "削除"),
        ("restore", "復元"),
    ]

    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name="histories",
        verbose_name="機材",
    )
    action = models.CharField(
        max_length=10, choices=ACTION_CHOICES, verbose_name="操作"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="操作者",
    )
    changes = models.TextField(blank=True, verbose_name="変更内容")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="操作日時")

    class Meta:
        verbose_name = "変更履歴"
        verbose_name_plural = "変更履歴"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.equipment.management_code} {self.get_action_display()} ({self.created_at:%Y-%m-%d %H:%M})"   
    
class Project(models.Model):
    """案件"""

    name = models.CharField(max_length=200, verbose_name="案件名")
    loan_date = models.DateField(verbose_name="貸出日")
    return_date = models.DateField(verbose_name="返却予定日")
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
        verbose_name="担当者",
    )
    notes = models.TextField(blank=True, verbose_name="備考")
    is_deleted = models.BooleanField(default=False, verbose_name="削除済み")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="登録日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        verbose_name = "案件"
        verbose_name_plural = "案件"
        ordering = ["-loan_date", "-id"]

    def __str__(self):
        return self.name

    @cached_property
    def _counts(self):
        """機材数を1回のクエリでまとめて集計する"""
        return self.assignments.aggregate(
            total=Count("id"),
            checked_out=Count("id", filter=Q(checked_out_at__isnull=False)),
            returned=Count("id", filter=Q(returned_at__isnull=False)),
        )

    @property
    def total_count(self):
        """登録機材の総数"""
        return self._counts["total"]

    @property
    def checked_out_count(self):
        """出庫済みの件数"""
        return self._counts["checked_out"]

    @property
    def returned_count(self):
        """返却済みの件数"""
        return self._counts["returned"]

    @cached_property
    def status(self):
        """状態を自動判定する（準備中 / 貸出中 / 返却待ち / 終了）"""
        total = self.total_count
        if total == 0:
            return "preparing"

        if self.returned_count >= total:
            return "finished"

        # 全機材が出庫済みでなければ準備中
        if self.checked_out_count < total:
            return "preparing"

        if timezone.localdate() > self.return_date:
            return "overdue"

        return "on_loan"
    @property
    def status_label(self):
        return {
            "preparing": "準備中",
            "on_loan": "貸出中",
            "overdue": "返却待ち",
            "finished": "終了",
        }.get(self.status, "")

    def overlaps_with(self, other_loan_date, other_return_date):
        """指定期間と重なるかどうか"""
        return self.loan_date <= other_return_date and other_loan_date <= self.return_date


class ProjectEquipment(models.Model):
    """案件への機材割り当て（予約票）"""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="案件",
    )
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="機材",
    )

    added_at = models.DateTimeField(auto_now_add=True, verbose_name="登録日時")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_assignments",
        verbose_name="登録者",
    )

    checked_out_at = models.DateTimeField(
        null=True, blank=True, verbose_name="出庫日時"
    )
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checked_out_assignments",
        verbose_name="出庫処理者",
    )

    returned_at = models.DateTimeField(
        null=True, blank=True, verbose_name="返却日時"
    )
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="returned_assignments",
        verbose_name="返却処理者",
    )

    class Meta:
        verbose_name = "案件機材"
        verbose_name_plural = "案件機材"
        unique_together = ("project", "equipment")
        ordering = ["equipment__management_code"]
        indexes = [
            models.Index(fields=["equipment", "returned_at"]),
            models.Index(fields=["project", "checked_out_at"]),
        ]

    def __str__(self):
        return f"{self.project.name} / {self.equipment.management_code}"

    @property
    def is_checked_out(self):
        return self.checked_out_at is not None and self.returned_at is None

    @property
    def is_returned(self):
        return self.returned_at is not None