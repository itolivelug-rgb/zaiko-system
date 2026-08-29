from django.contrib import admin
from .models import Area, Kind, StorageLocation, Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("management_code", "model_name", "area", "kind", "storage_location")
    list_filter = ("area", "kind", "storage_location")
    search_fields = ("management_code", "model_name", "name")


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    list_editable = ("name",)
    ordering = ("code",)


@admin.register(Kind)
class KindAdmin(admin.ModelAdmin):
    list_display = ("area", "sort_order", "name")
    list_editable = ("sort_order",)
    list_filter = ("area",)
    ordering = ("area__code", "sort_order")


admin.site.register(StorageLocation)

from .models import Project, ProjectEquipment


class ProjectEquipmentInline(admin.TabularInline):
    model = ProjectEquipment
    extra = 0
    raw_id_fields = ("equipment",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "loan_date", "return_date", "manager", "status_label", "is_deleted")
    list_filter = ("is_deleted",)
    search_fields = ("name",)
    inlines = [ProjectEquipmentInline]