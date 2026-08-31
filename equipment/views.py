import csv
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from .forms import EquipmentForm, ProjectForm, UserCreateForm, UserUpdateForm
from .models import (
    Area, Equipment, EquipmentHistory, Kind,
    Project, ProjectEquipment, StorageLocation,
)


# 並び替えを許可する列（不正な値を弾くためのホワイトリスト）
SORTABLE_FIELDS = {
    "management_code",
    "model_name",
    "name",
    "area__name",
    "kind__name",
    "storage_location__name",
}
def gantt_state(project):
    """ガント表示用の状態を返す（返却待ちは貸出中に含める）"""
    if project.total_count > 0 and project.returned_count >= project.total_count:
        return "finished", "終了"
    if project.checked_out_count > 0:
        return "on_loan", "貸出中"
    return "preparing", "準備中"

def filter_equipments(request):
    """絞り込み・並び替えを適用した機材一覧を返す（一覧・印刷・CSV共通）"""
    equipments = Equipment.objects.select_related("area", "kind", "storage_location")

    show_deleted = request.GET.get("deleted") == "1"
    equipments = equipments.filter(is_deleted=show_deleted)

    # 検索（スペース区切りでAND検索）
    keyword = request.GET.get("q", "").strip()
    if keyword:
        for word in keyword.replace("　", " ").split():
            equipments = equipments.filter(
                Q(management_code__icontains=word)
                | Q(model_name__icontains=word)
                | Q(name__icontains=word)
            )

    # 絞り込み
    area_id = request.GET.get("area", "")
    kind_id = request.GET.get("kind", "")
    storage_id = request.GET.get("storage", "")

    if area_id:
        equipments = equipments.filter(area_id=area_id)
    if kind_id:
        equipments = equipments.filter(kind_id=kind_id)
    if storage_id:
        equipments = equipments.filter(storage_location_id=storage_id)
        
    # --- 貸出状況で絞り込み ---
    loan_status = request.GET.get("loan", "")
    if loan_status:
        active = ProjectEquipment.objects.filter(
            returned_at__isnull=True, project__is_deleted=False
        )
        if loan_status == "onloan":
            ids = active.filter(checked_out_at__isnull=False).values_list("equipment_id", flat=True)
            equipments = equipments.filter(id__in=ids)
        elif loan_status == "reserved":
            on_loan_ids = active.filter(checked_out_at__isnull=False).values_list("equipment_id", flat=True)
            reserved_ids = active.filter(checked_out_at__isnull=True).values_list("equipment_id", flat=True)
            equipments = equipments.filter(id__in=reserved_ids).exclude(id__in=on_loan_ids)
        elif loan_status == "instock":
            busy_ids = active.values_list("equipment_id", flat=True)
            equipments = equipments.exclude(id__in=busy_ids)

    # --- 案件で絞り込み ---
    project_id = request.GET.get("project", "")
    if project_id:
        ids = ProjectEquipment.objects.filter(
            project_id=project_id
        ).values_list("equipment_id", flat=True)
        equipments = equipments.filter(id__in=ids)

    # 並び替え
    sort = request.GET.get("sort", "management_code")
    direction = request.GET.get("dir", "asc")
    if sort not in SORTABLE_FIELDS:
        sort = "management_code"
    order_by = sort if direction == "asc" else f"-{sort}"
    equipments = equipments.order_by(order_by)

    conditions = {
        "keyword": keyword,
        "area_id": area_id,
        "kind_id": kind_id,
        "storage_id": storage_id,
        "sort": sort,
        "direction": direction,
        "show_deleted": show_deleted,
        "loan_status": loan_status,
        "project_id": project_id,
    }
    return equipments, conditions


@login_required
def equipment_list(request):
    equipments, cond = filter_equipments(request)

    paginator = Paginator(equipments, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    # 表示中の機材の貸出状況をまとめて取得する
    page_ids = [e.id for e in page_obj]
    assignments = ProjectEquipment.objects.filter(
        equipment_id__in=page_ids,
        returned_at__isnull=True,
        project__is_deleted=False,
    ).select_related("project")

    loan_info = {}
    for a in assignments:
        info = loan_info.setdefault(a.equipment_id, {"projects": [], "on_loan": False})
        info["projects"].append(a.project.name)
        if a.checked_out_at:
            info["on_loan"] = True

    rows = []
    for e in page_obj:
        info = loan_info.get(e.id)
        if not info:
            status = "予約なし"
            status_key = "instock"
            projects = ""
        elif info["on_loan"]:
            status = "貸出中"
            status_key = "onloan"
            projects = "、".join(info["projects"])
        else:
            status = "準備中"
            status_key = "reserved"
            projects = "、".join(info["projects"])

        rows.append({
            "equipment": e,
            "loan_status": status,
            "loan_status_key": status_key,
            "project_names": projects,
        })

    kinds = Kind.objects.select_related("area").all()

    pager_params = urlencode({
        "q": cond["keyword"],
        "area": cond["area_id"],
        "kind": cond["kind_id"],
        "storage": cond["storage_id"],
        "loan": cond["loan_status"],
        "project": cond["project_id"],
        "sort": cond["sort"],
        "dir": cond["direction"],
        "deleted": "1" if cond["show_deleted"] else "",
    })

    context = {
        "page_obj": page_obj,
        "rows": rows,
        "total_count": paginator.count,
        "areas": Area.objects.all(),
        "all_kinds": kinds,
        "storages": StorageLocation.objects.all(),
        "keyword": cond["keyword"],
        "selected_area": cond["area_id"],
        "selected_kind": cond["kind_id"],
        "selected_storage": cond["storage_id"],
        "sort": cond["sort"],
        "direction": cond["direction"],
        "show_deleted": cond["show_deleted"],
        "current_url": request.get_full_path(),
        "selected_loan": cond["loan_status"],
        "selected_project": cond["project_id"],
        "projects": [p for p in Project.objects.filter(is_deleted=False).order_by("loan_date") if p.status != "finished"],
        "pager_params": pager_params,
    }
    return render(request, "equipment/equipment_list.html", context)

def staff_required(view_func):
        """管理者（is_staff）のみアクセスを許可するデコレーター"""
        def wrapper(request, *args, **kwargs):
            if not request.user.is_staff:
                raise PermissionDenied("この操作には管理者権限が必要です。")
            return view_func(request, *args, **kwargs)
        return wrapper


@login_required
@staff_required
def user_list(request):
    """ユーザー一覧"""
    users = User.objects.all().order_by("username")
    return render(request, "equipment/user_list.html", {"users": users})


@login_required
@staff_required
def user_create(request):
    """ユーザー新規作成"""
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"ユーザー「{user.username}」を作成しました。")
            return redirect("user_list")
    else:
        form = UserCreateForm()

    return render(request, "equipment/user_form.html", {
        "form": form,
        "title": "ユーザー新規作成",
    })


@login_required
@staff_required
def user_update(request, pk):
    """ユーザー編集"""
    target_user = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=target_user)
        if form.is_valid():
            form.save()
            messages.success(request, f"ユーザー「{target_user.username}」を更新しました。")
            return redirect("user_list")
    else:
        form = UserUpdateForm(instance=target_user)

    return render(request, "equipment/user_form.html", {
        "form": form,
        "title": f"ユーザー編集: {target_user.username}",
        "target_user": target_user,
    })


@login_required
@staff_required
def user_password(request, pk):
    """パスワード再設定"""
    target_user = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = SetPasswordForm(target_user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"「{target_user.username}」のパスワードを変更しました。")
            return redirect("user_list")
    else:
        form = SetPasswordForm(target_user)

    return render(request, "equipment/user_form.html", {
        "form": form,
        "title": f"パスワード再設定: {target_user.username}",
    })

def build_change_log(old_data, instance):
    """変更前後を比較して、変更内容の文字列を作る"""
    labels = {
        "management_code": "品目コード",
        "area": "領域",
        "kind": "種別",
        "model_name": "機種",
        "name": "名称",
        "storage_location": "保管場所",
        "notes": "備考",
    }
    lines = []
    for field, label in labels.items():
        old_value = old_data.get(field, "")
        new_value = getattr(instance, field, "")
        old_str = str(old_value) if old_value else "(空)"
        new_str = str(new_value) if new_value else "(空)"
        if old_str != new_str:
            lines.append(f"{label}: {old_str} → {new_str}")
    return "\n".join(lines)


def snapshot(instance):
    """現在の値を記録しておく（変更前の状態を保存するため）"""
    return {
        "management_code": instance.management_code,
        "area": instance.area,
        "kind": instance.kind,
        "model_name": instance.model_name,
        "name": instance.name,
        "storage_location": instance.storage_location,
        "notes": instance.notes,
    }

@login_required
def equipment_create(request):
    """機材の新規登録"""
    return_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        form = EquipmentForm(request.POST)
        if form.is_valid():
            equipment = form.save()
            EquipmentHistory.objects.create(
                equipment=equipment,
                action="create",
                user=request.user,
                changes="新規登録しました。",
            )
            messages.success(request, f"「{equipment.management_code}」を登録しました。")

            action = request.POST.get("action", "save")
            if action == "continue_same":
                # 同じ機種を続けて登録（領域・種別・機種を引き継ぐ）
                params = urlencode({
                    "next": return_url,
                    "area": equipment.area_id,
                    "kind": equipment.kind_id,
                    "model": equipment.management_code[:7],
                })
                return redirect(f"{reverse('equipment_create')}?{params}")
            if action == "continue":
                # 続けて登録（空のフォーム）
                params = urlencode({"next": return_url})
                return redirect(f"{reverse('equipment_create')}?{params}")

            return redirect(return_url or "equipment_list")
    else:
        form = EquipmentForm()

    return render(request, "equipment/equipment_form.html", {
        "form": form,
        "title": "機材の新規登録",
        "all_kinds": Kind.objects.select_related("area").all(),
        "return_url": return_url,
        "preset_area": request.GET.get("area", ""),
        "preset_kind": request.GET.get("kind", ""),
        "preset_model": request.GET.get("model", ""),
    })


@login_required
def equipment_update(request, pk):
    """機材の編集"""
    equipment = get_object_or_404(Equipment, pk=pk)
    return_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        old_data = snapshot(equipment)
        form = EquipmentForm(request.POST, instance=equipment)
        if form.is_valid():
            equipment = form.save()
            changes = build_change_log(old_data, equipment)
            if changes:
                EquipmentHistory.objects.create(
                    equipment=equipment,
                    action="update",
                    user=request.user,
                    changes=changes,
                )
            messages.success(request, f"「{equipment.management_code}」を更新しました。")
            return redirect(return_url or "equipment_list")
    else:
        form = EquipmentForm(instance=equipment)

    return render(request, "equipment/equipment_form.html", {
        "form": form,
        "title": f"機材の編集: {equipment.management_code}",
        "equipment": equipment,
        "all_kinds": Kind.objects.select_related("area").all(),
        "return_url": return_url,
    })

@login_required
def equipment_delete(request, pk):
    """機材の削除（論理削除）"""
    equipment = get_object_or_404(Equipment, pk=pk)
    return_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        equipment.is_deleted = True
        equipment.save()
        EquipmentHistory.objects.create(
            equipment=equipment,
            action="delete",
            user=request.user,
            changes="削除しました。",
        )
        messages.success(request, f"「{equipment.management_code}」を削除しました。")
        return redirect(return_url or "equipment_list")

    return render(request, "equipment/equipment_delete.html", {
        "equipment": equipment,
        "return_url": return_url,
    })


@login_required
def equipment_restore(request, pk):
    """削除済み機材の復元"""
    equipment = get_object_or_404(Equipment, pk=pk)
    return_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.method == "POST":
        # 同じ品目コードが既に使われていないか確認
        conflict = Equipment.objects.filter(
            management_code=equipment.management_code, is_deleted=False
        ).exclude(pk=equipment.pk).exists()

        if conflict:
            messages.error(
                request,
                f"「{equipment.management_code}」は別の機材で使用されているため復元できません。"
            )
            return redirect(return_url or "equipment_list")

        equipment.is_deleted = False
        equipment.save()
        EquipmentHistory.objects.create(
            equipment=equipment,
            action="restore",
            user=request.user,
            changes="復元しました。",
        )
        messages.success(request, f"「{equipment.management_code}」を復元しました。")
        return redirect(return_url or "equipment_list")

    return redirect(return_url or "equipment_list")


@login_required
def equipment_history(request, pk):
    """機材の更新履歴"""
    equipment = get_object_or_404(Equipment, pk=pk)
    histories = equipment.histories.select_related("user").all()
    return_url = request.GET.get("next") or ""

    return render(request, "equipment/equipment_history.html", {
        "equipment": equipment,
        "histories": histories,
        "return_url": return_url,
    })

@login_required
def home(request):
    """ホーム画面"""
    return render(request, "equipment/home.html")

# 出力可能な列の定義（キー: (見出し, 値の取り出し方)）
EXPORT_COLUMNS = {
    "management_code": ("品目コード", lambda e: e.management_code),
    "area":            ("領域",       lambda e: e.area.name),
    "kind":            ("種別",       lambda e: e.kind.name),
    "model_name":      ("機種",       lambda e: e.model_name),
    "name":            ("名称",       lambda e: e.name),
    "storage":         ("保管場所",   lambda e: e.storage_location.name if e.storage_location else ""),
    "loan_status":     ("貸出状況",   lambda e: e.loan_status_label),
    "project":         ("登録案件",   lambda e: e.project_names),
    "notes":           ("備考",       lambda e: e.notes),
}

DEFAULT_EXPORT_COLUMNS = ["management_code", "area", "kind", "model_name", "name", "storage"]


def get_export_columns(request):
    """URLパラメータから出力する列を決める"""
    raw = request.GET.get("cols", "")
    keys = [k for k in raw.split(",") if k in EXPORT_COLUMNS]
    return keys or DEFAULT_EXPORT_COLUMNS


def describe_conditions(cond):
    """絞り込み条件を人が読める文字列にする"""
    parts = []
    if cond["keyword"]:
        parts.append(f"検索: {cond['keyword']}")
    if cond["area_id"]:
        area = Area.objects.filter(id=cond["area_id"]).first()
        if area:
            parts.append(f"領域: {area.name}")
    if cond["kind_id"]:
        kind = Kind.objects.filter(id=cond["kind_id"]).first()
        if kind:
            parts.append(f"種別: {kind.name}")
    if cond["storage_id"]:
        storage = StorageLocation.objects.filter(id=cond["storage_id"]).first()
        if storage:
            parts.append(f"保管場所: {storage.name}")
    if cond["show_deleted"]:
        parts.append("削除済みのみ")
    return " / ".join(parts) if parts else "絞り込みなし（全件）"


@login_required
def equipment_print(request):
    """印刷用ページ（絞り込み結果の全件）"""
    equipments, cond = filter_equipments(request)
    col_keys = get_export_columns(request)

    # 印刷時は領域ごとにまとめる（領域内は画面の並び順を維持）
    inner_sort = cond["sort"] if cond["direction"] == "asc" else f"-{cond['sort']}"
    equipments = equipments.order_by("area__id", inner_sort)

    headers = [EXPORT_COLUMNS[k][0] for k in col_keys]

    rows = []
    current_area = None
    stripe = 0
    for e in equipments:
        area_name = e.area.name
        if area_name != current_area:
            rows.append({"type": "tab", "label": area_name})
            current_area = area_name
            stripe = 0
        rows.append({
            "type": "data",
            "cells": [
                {"value": EXPORT_COLUMNS[k][1](e), "key": k}
                for k in col_keys
            ],
            "odd": stripe % 2 == 1,
        })
        stripe += 1

    data_count = sum(1 for r in rows if r["type"] == "data")

    return render(request, "equipment/equipment_print.html", {
        "headers": headers,
        "rows": rows,
        "col_count": len(col_keys),
        "total_count": data_count,
        "condition_text": describe_conditions(cond),
        "printed_at": timezone.localtime(),
        "page_title": "品目一覧",
    })

@login_required
def equipment_csv(request):
    """CSV出力（絞り込み結果の全件）"""
    equipments, cond = filter_equipments(request)
    col_keys = get_export_columns(request)

    response = HttpResponse(content_type="text/csv; charset=cp932")
    response.charset = "cp932"
    filename = f"equipment_{timezone.localtime():%Y%m%d_%H%M}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Excelで開いたときに文字化けしないようCP932で書き出す
    writer = csv.writer(response, lineterminator="\r\n")
    writer.writerow([EXPORT_COLUMNS[k][0] for k in col_keys])
    for e in equipments:
        writer.writerow([EXPORT_COLUMNS[k][1](e) for k in col_keys])

    return response

@login_required
@staff_required
def equipment_label_csv(request):
    """テプラ用ラベルCSV出力（管理者のみ）"""
    equipments, cond = filter_equipments(request)

    # ラベル印刷では領域ごとにまとめる
    inner_sort = cond["sort"] if cond["direction"] == "asc" else f"-{cond['sort']}"
    equipments = equipments.order_by("area__id", inner_sort)

    response = HttpResponse(content_type="text/csv; charset=cp932")
    response.charset = "cp932"
    filename = f"label_{timezone.localtime():%Y%m%d_%H%M}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, lineterminator="\r\n")
    for e in equipments:
        writer.writerow([
            e.management_code,
            e.area.name,
            e.name,
            e.model_name,
        ])

    return response

@login_required
def history_list(request):
    """全機材の更新履歴一覧"""
    histories = EquipmentHistory.objects.select_related(
        "equipment", "equipment__area", "user"
    )

    # 検索（品目コード・機種）
    keyword = request.GET.get("q", "").strip()
    if keyword:
        for word in keyword.replace("　", " ").split():
            histories = histories.filter(
                Q(equipment__management_code__icontains=word)
                | Q(equipment__model_name__icontains=word)
            )

    # 絞り込み
    action = request.GET.get("action", "")
    user_id = request.GET.get("user", "")

    if action:
        histories = histories.filter(action=action)
    if user_id:
        histories = histories.filter(user_id=user_id)

    histories = histories.order_by("-created_at")

    paginator = Paginator(histories, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    # 絞り込み用の操作者リスト（履歴を持つユーザーのみ）
    operator_ids = EquipmentHistory.objects.values_list("user_id", flat=True).distinct()
    operators = User.objects.filter(id__in=operator_ids).order_by("username")

    pager_params = urlencode({
        "q": keyword,
        "action": action,
        "user": user_id,
    })
    context = {
        "page_obj": page_obj,
        "total_count": paginator.count,
        "keyword": keyword,
        "selected_action": action,
        "selected_user": user_id,
        "action_choices": EquipmentHistory.ACTION_CHOICES,
        "operators": operators,
        "current_url": request.get_full_path(),
        "pager_params": pager_params,
    }
    return render(request, "equipment/history_list.html", context)

@login_required
def project_list(request):
    """案件一覧"""
    show_deleted = request.GET.get("deleted") == "1"
    projects = Project.objects.filter(is_deleted=show_deleted).select_related("manager")

    keyword = request.GET.get("q", "").strip()
    if keyword:
        for word in keyword.replace("　", " ").split():
            projects = projects.filter(name__icontains=word)

    # 進行中と終了で分ける（削除済み表示時は分けない）
    active, finished = [], []
    for p in projects:
        if p.status == "finished":
            finished.append(p)
        else:
            active.append(p)

    # 進行中は貸出日の近い順
    active.sort(key=lambda p: p.loan_date)

    return render(request, "equipment/project_list.html", {
        "active_projects": active,
        "finished_projects": finished,
        "show_deleted": show_deleted,
        "keyword": keyword,
        "current_url": request.get_full_path(),
    })


@login_required
def project_create(request):
    """案件の新規登録"""
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.manager = request.user
            project.save()
            messages.success(request, f"案件「{project.name}」を登録しました。機材を追加してください。")
            return redirect("project_detail", pk=project.pk)
    else:
        form = ProjectForm()

    return render(request, "equipment/project_form.html", {
        "form": form,
        "title": "案件の新規登録",
    })


@login_required
def project_update(request, pk):
    """案件の編集"""
    project = get_object_or_404(Project, pk=pk)
    
    if project.status == "finished":
        messages.error(request, "終了した案件は編集できません。")
        return redirect("project_detail", pk=project.pk)

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, f"案件「{project.name}」を更新しました。")
            return redirect("project_detail", pk=project.pk)
    else:
        form = ProjectForm(instance=project)

    return render(request, "equipment/project_form.html", {
        "form": form,
        "title": f"案件の編集: {project.name}",
        "project": project,
    })


@login_required
def project_detail(request, pk):
    """案件詳細（登録機材の一覧）"""
    project = get_object_or_404(Project, pk=pk)
    assignments = project.assignments.select_related(
        "equipment", "equipment__area", "equipment__kind", "equipment__storage_location"
    ).order_by("equipment__area__code", "equipment__kind__sort_order", "equipment__management_code")

    # 領域ごとにまとめる
    grouped = []
    current_area = None
    for a in assignments:
        area_name = a.equipment.area.name
        if area_name != current_area:
            grouped.append({"area": area_name, "items": []})
            current_area = area_name
        grouped[-1]["items"].append(a)

    return render(request, "equipment/project_detail.html", {
        "project": project,
        "grouped": grouped,
        "assignments": assignments,
    })


@login_required
def project_delete(request, pk):
    """案件の削除（論理削除）"""
    project = get_object_or_404(Project, pk=pk)

    if request.method == "POST":
        project.is_deleted = True
        project.save()
        messages.success(request, f"案件「{project.name}」を削除しました。予約されていた機材は解放されます。")
        return redirect("project_list")

    return render(request, "equipment/project_delete.html", {"project": project})


@login_required
def project_restore(request, pk):
    """削除済み案件の復元"""
    project = get_object_or_404(Project, pk=pk)

    if request.method == "POST":
        project.is_deleted = False
        project.save()
        messages.success(request, f"案件「{project.name}」を復元しました。機材の重複がないか確認してください。")
        return redirect("project_detail", pk=project.pk)

    return redirect("project_list")

@login_required
def project_add_equipment(request, pk):
    """案件への機材追加（品目一覧ベース、重複判定つき）"""
    project = get_object_or_404(Project, pk=pk)

    if project.status == "finished":
        messages.error(request, "終了した案件の機材は変更できません。")
        return redirect("project_detail", pk=project.pk)

    # --- POST: 表示中の機材について、チェック状態を同期する ---
    if request.method == "POST":
        checked_ids = set(int(i) for i in request.POST.getlist("equipment_ids"))
        shown_ids = set(int(i) for i in request.POST.getlist("shown_ids"))

        added, removed, skipped = 0, 0, 0

        # チェックが外れたもの → 案件から解除
        to_remove = shown_ids - checked_ids
        if to_remove:
            removable = project.assignments.filter(equipment_id__in=to_remove)
            removed = removable.count()
            removable.delete()

        # チェックが入ったもの → 未登録なら追加
        already = set(project.assignments.values_list("equipment_id", flat=True))
        for eq_id in checked_ids - already:
            equipment = Equipment.objects.filter(pk=eq_id, is_deleted=False).first()
            if not equipment:
                continue

            conflict = ProjectEquipment.objects.filter(
                equipment=equipment,
                returned_at__isnull=True,
                project__is_deleted=False,
                project__loan_date__lte=project.return_date,
                project__return_date__gte=project.loan_date,
            ).exclude(project=project).exists()

            if conflict:
                skipped += 1
                continue

            ProjectEquipment.objects.create(
                project=project,
                equipment=equipment,
                added_by=request.user,
            )
            added += 1

        parts = []
        if added:
            parts.append(f"{added} 点を追加")
        if removed:
            parts.append(f"{removed} 点を解除")
        if parts:
            messages.success(request, "、".join(parts) + "しました。")
        if skipped:
            messages.error(request, f"{skipped} 点は他案件と期間が重なるため追加できませんでした。")

        return redirect(f"{request.path}?{request.POST.get('query_string', '')}")

    # --- GET: 選択画面を表示 ---
    equipments, cond = filter_equipments(request)

    # 表示期間（デフォルトは案件の前後1週間）
    def parse_date(value, default):
        if value:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                pass
        return default

    range_start = parse_date(request.GET.get("from"), project.loan_date - timedelta(days=7))
    range_end = parse_date(request.GET.get("to"), project.return_date + timedelta(days=7))

    if range_end < range_start:
        range_end = range_start

    # 表示日数の上限（描画が重くなりすぎないように）
    max_days = 92
    if (range_end - range_start).days + 1 > max_days:
        range_end = range_start + timedelta(days=max_days - 1)

    days = []
    total_days = (range_end - range_start).days + 1
    for i in range(total_days):
        d = range_start + timedelta(days=i)
        days.append({
            "date": d,
            "weekend": d.weekday() >= 5,
            "in_project": project.loan_date <= d <= project.return_date,
            "is_start": d == project.loan_date,
            "is_end": d == project.return_date,
        })

    # 1日あたりの最小幅を決める（デフォルト期間がちょうど収まる幅を基準にする）
    default_days = (project.return_date - project.loan_date).days + 15  # 前後7日ずつ
    gantt_min_width = max(700, int(total_days / default_days * 700))
    
    today = timezone.localdate()

    # この案件に登録済み・出庫済みの機材ID
    assigned_ids = set(project.assignments.values_list("equipment_id", flat=True))
    checked_out_ids = set(
        project.assignments.filter(checked_out_at__isnull=False).values_list("equipment_id", flat=True)
    )

    paginator = Paginator(equipments, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_ids = [e.id for e in page_obj]

    # 表示期間に関わる他案件の割り当てをまとめて取得
    bars_by_equipment = {}
    conflict_ids = set()

    other_qs = ProjectEquipment.objects.filter(
        equipment_id__in=page_ids,
        project__is_deleted=False,
        project__loan_date__lte=range_end,
        project__return_date__gte=range_start,
    ).exclude(project=project).select_related("project")

    for pe in other_qs:
        p = pe.project

        # 帯の位置（%）を計算する
        bar_start = max(p.loan_date, range_start)
        bar_end = min(p.return_date, range_end)
        left = (bar_start - range_start).days / total_days * 100
        width = ((bar_end - bar_start).days + 1) / total_days * 100

        # 状態を判定（返却待ちは貸出中に含める）
        state, state_label = gantt_state(p)

        bars_by_equipment.setdefault(pe.equipment_id, []).append({
            "name": p.name,
            "left": round(left, 4),
            "width": round(width, 4),
            "state": state,
            "state_label": state_label,
        })

        # 編集中の案件と期間が重なるなら使用不可
        if pe.returned_at is None and p.loan_date <= project.return_date and project.loan_date <= p.return_date:
            conflict_ids.add(pe.equipment_id)

    # 編集中案件の帯の位置
    self_left = (max(project.loan_date, range_start) - range_start).days / total_days * 100
    self_end = min(project.return_date, range_end)
    self_width = ((self_end - max(project.loan_date, range_start)).days + 1) / total_days * 100
    self_visible = project.loan_date <= range_end and project.return_date >= range_start

    rows = []
    for e in page_obj:
        rows.append({
            "equipment": e,
            "assigned": e.id in assigned_ids,
            "checked_out": e.id in checked_out_ids,
            "conflict": e.id in conflict_ids,
            "bars": bars_by_equipment.get(e.id, []),
        })

    pager_params = urlencode({
        "q": cond["keyword"],
        "area": cond["area_id"],
        "kind": cond["kind_id"],
        "storage": cond["storage_id"],
        "loan": cond["loan_status"],
        "project": cond["project_id"],
        "sort": cond["sort"],
        "dir": cond["direction"],
        "from": range_start.isoformat(),
        "to": range_end.isoformat(),
    })

    return render(request, "equipment/project_add_equipment.html", {
        "project": project,
        "page_obj": page_obj,
        "rows": rows,
        "days": days,
        "total_days": total_days,
        "range_start": range_start,
        "range_end": range_end,
        "self_left": round(self_left, 4),
        "self_width": round(self_width, 4),
        "self_visible": self_visible,
        "today": today,
        "total_count": paginator.count,
        "areas": Area.objects.all(),
        "all_kinds": Kind.objects.select_related("area").all(),
        "storages": StorageLocation.objects.all(),
        "keyword": cond["keyword"],
        "selected_area": cond["area_id"],
        "selected_kind": cond["kind_id"],
        "selected_storage": cond["storage_id"],
        "sort": cond["sort"],
        "direction": cond["direction"],
        "pager_params": pager_params,
        "query_string": request.GET.urlencode(),
        "gantt_min_width": gantt_min_width,
        "selected_loan": cond["loan_status"],
        "selected_project": cond["project_id"],
        "projects": [p for p in Project.objects.filter(is_deleted=False).order_by("loan_date") if p.status != "finished"],            
    })

@login_required
def project_print(request, pk):
    """案件の機材リスト印刷"""
    project = get_object_or_404(Project, pk=pk)

    assignments = project.assignments.select_related(
        "equipment", "equipment__area", "equipment__kind", "equipment__storage_location"
    ).order_by("equipment__management_code")

    # 載替判定：前案件の返却予定日の翌日が、この案件の貸出日の場合
    prev_day = project.loan_date - timedelta(days=1)
    prev_map = {}
    prev_qs = ProjectEquipment.objects.filter(
        equipment_id__in=[a.equipment_id for a in assignments],
        project__is_deleted=False,
        project__return_date=prev_day,
    ).exclude(project=project).select_related("project")

    for pe in prev_qs:
        prev_map[pe.equipment_id] = pe.project.name

    # 領域が切り替わる位置にタブ行を挟む
    rows = []
    current_area = None
    stripe = 0
    for a in assignments:
        e = a.equipment
        area_name = e.area.name
        if area_name != current_area:
            rows.append({"type": "tab", "label": area_name})
            current_area = area_name
            stripe = 0
        rows.append({
            "type": "data",
            "cells": [
                e.management_code,
                e.area.name,
                e.kind.name,
                e.model_name,
                e.name,
                e.storage_location.name if e.storage_location else "",
            ],
            "transfer": prev_map.get(e.id, ""),
            "odd": stripe % 2 == 1,
        })
        stripe += 1

    data_count = sum(1 for r in rows if r["type"] == "data")

    user = request.user
    manager_name = f"{user.last_name}{user.first_name}".strip() or user.username

    return render(request, "equipment/project_print.html", {
        "project": project,
        "headers": ["品目コード", "領域", "種別", "機種", "名称", "保管場所"],
        "rows": rows,
        "col_count": 7,  # 載替列を含む
        "total_count": data_count,
        "manager_name": manager_name,
        "printed_at": timezone.localtime(),
        "notes": project.notes,
    })
    
@login_required
def equipment_gantt(request):
    """品目一覧のガントチャート表示"""
    equipments, cond = filter_equipments(request)

    today = timezone.localdate()

    def parse_date(value, default):
        if value:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                pass
        return default

    range_start = parse_date(request.GET.get("from"), today - timedelta(days=7))
    range_end = parse_date(request.GET.get("to"), today + timedelta(days=21))

    if range_end < range_start:
        range_end = range_start

    max_days = 92
    if (range_end - range_start).days + 1 > max_days:
        range_end = range_start + timedelta(days=max_days - 1)

    total_days = (range_end - range_start).days + 1
    days = []
    for i in range(total_days):
        d = range_start + timedelta(days=i)
        days.append({
            "date": d,
            "weekend": d.weekday() >= 5,
            "is_today": d == today,
        })

    paginator = Paginator(equipments, 100)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_ids = [e.id for e in page_obj]

    bars_by_equipment = {}
    qs = ProjectEquipment.objects.filter(
        equipment_id__in=page_ids,
        project__is_deleted=False,
        project__loan_date__lte=range_end,
        project__return_date__gte=range_start,
    ).select_related("project")
    for pe in qs:
        p = pe.project
        bar_start = max(p.loan_date, range_start)
        bar_end = min(p.return_date, range_end)
        left = (bar_start - range_start).days / total_days * 100
        width = ((bar_end - bar_start).days + 1) / total_days * 100

        state, state_label = gantt_state(p)

        bars_by_equipment.setdefault(pe.equipment_id, []).append({
            "name": p.name,
            "left": round(left, 4),
            "width": round(width, 4),
            "state": state,
            "state_label": state_label,
        })

    rows = []
    for e in page_obj:
        rows.append({
            "equipment": e,
            "bars": bars_by_equipment.get(e.id, []),
        })
    # 1日あたりの幅を保つ（28日分がちょうど収まる幅を基準にする）
    gantt_min_width = max(700, int(total_days / 28 * 700))

    pager_params = urlencode({
        "q": cond["keyword"],
        "area": cond["area_id"],
        "kind": cond["kind_id"],
        "storage": cond["storage_id"],
        "loan": cond["loan_status"],
        "project": cond["project_id"],
        "sort": cond["sort"],
        "dir": cond["direction"],
        "from": range_start.isoformat(),
        "to": range_end.isoformat(),
    })

    return render(request, "equipment/equipment_gantt.html", {
        "page_obj": page_obj,
        "rows": rows,
        "days": days,
        "total_days": total_days,
        "range_start": range_start,
        "range_end": range_end,
        "gantt_min_width": gantt_min_width,
        "total_count": paginator.count,
        "areas": Area.objects.all(),
        "all_kinds": Kind.objects.select_related("area").all(),
        "storages": StorageLocation.objects.all(),
        "keyword": cond["keyword"],
        "selected_area": cond["area_id"],
        "selected_kind": cond["kind_id"],
        "selected_storage": cond["storage_id"],
        "selected_loan": cond["loan_status"],
        "selected_project": cond["project_id"],
        "projects": [p for p in Project.objects.filter(is_deleted=False).order_by("loan_date") if p.status != "finished"],
        "pager_params": pager_params,
        "current_url": request.get_full_path(),
    })
    
@login_required
def loan_project_list(request):
    """貸出：案件選択"""
    projects = Project.objects.filter(is_deleted=False).select_related("manager")

    keyword = request.GET.get("q", "").strip()
    if keyword:
        for word in keyword.replace("　", " ").split():
            projects = projects.filter(name__icontains=word)

    # 出庫が完了していない案件を優先し、貸出日の近い順に並べる
    pending, others = [], []
    for p in projects:
        if p.total_count == 0:
            continue
        if p.checked_out_count < p.total_count:
            pending.append(p)
        elif p.returned_count < p.total_count:
            others.append(p)

    pending.sort(key=lambda p: p.loan_date)
    others.sort(key=lambda p: p.loan_date)

    return render(request, "equipment/loan_project_list.html", {
        "pending_projects": pending,
        "other_projects": others,
        "keyword": keyword,
    })


@login_required
def loan_scan(request, pk):
    """貸出：スキャン画面"""
    project = get_object_or_404(Project, pk=pk, is_deleted=False)

    # --- POST: 読み込んだ機材を出庫確定 ---
    if request.method == "POST":
        ids = request.POST.getlist("scanned_ids")
        now = timezone.now()
        count = 0

        for eq_id in ids:
            assignment = project.assignments.filter(
                equipment_id=eq_id, checked_out_at__isnull=True
            ).first()
            if assignment:
                assignment.checked_out_at = now
                assignment.checked_out_by = request.user
                assignment.save()
                count += 1

        if count:
            messages.success(request, f"{count} 点を貸出処理しました。")
        return redirect("loan_scan", pk=project.pk)
    
    # --- GET: 案件の全機材を取得（出庫済みは読み込み済み側に置く） ---
    assignments = project.assignments.select_related(
        "equipment", "equipment__area", "equipment__kind", "equipment__storage_location"
    ).order_by("equipment__management_code")

    items = []
    done_codes = []
    for a in assignments:
        e = a.equipment
        items.append({
            "id": e.id,
            "code": e.management_code,
            "area": e.area.name,
            "kind": e.kind.name,
            "model": e.model_name,
            "name": e.name,
            "storage": e.storage_location.name if e.storage_location else "",
            "done": a.checked_out_at is not None,
        })
        if a.checked_out_at is not None:
            done_codes.append(e.management_code)

    return render(request, "equipment/loan_scan.html", {
        "project": project,
        "items": items,
        "items_json": json.dumps(items, ensure_ascii=False),
        "done_json": json.dumps(done_codes, ensure_ascii=False),
    })
    
@login_required
def return_project_list(request):
    """返却：案件選択"""
    projects = Project.objects.filter(is_deleted=False).select_related("manager")

    keyword = request.GET.get("q", "").strip()
    if keyword:
        for word in keyword.replace("　", " ").split():
            projects = projects.filter(name__icontains=word)

    today = timezone.localdate()
    overdue, normal, loading, recent = [], [], [], []

    for p in projects:
        total = p.total_count
        if total == 0:
            continue

        checked_out = p.checked_out_count
        returned = p.returned_count

        # 全点返却済み → 直近1週間だけ表示
        if returned >= total:
            last = p.assignments.filter(returned_at__isnull=False).order_by("-returned_at").first()
            if last and (today - timezone.localdate(last.returned_at)).days <= 7:
                p.returned_on = timezone.localdate(last.returned_at)
                recent.append(p)
            continue

        # 出庫が完了していない → 貸出処理中
        if checked_out < total:
            p.checked_out_count_cache = checked_out
            loading.append(p)
            continue

        # 全点出庫済みで未返却あり
        p.pending_return = checked_out - returned
        if today > p.return_date:
            overdue.append(p)
        else:
            normal.append(p)

    overdue.sort(key=lambda p: p.return_date)
    normal.sort(key=lambda p: p.return_date)
    loading.sort(key=lambda p: p.return_date)
    recent.sort(key=lambda p: p.returned_on, reverse=True)

    return render(request, "equipment/return_project_list.html", {
        "overdue_projects": overdue,
        "normal_projects": normal,
        "loading_projects": loading,
        "recent_projects": recent,
        "keyword": keyword,
    })


@login_required
def return_scan(request, pk):
    """返却：スキャン画面"""
    project = get_object_or_404(Project, pk=pk, is_deleted=False)

    # --- POST: 読み込んだ機材を返却確定 ---
    if request.method == "POST":
        ids = request.POST.getlist("scanned_ids")
        now = timezone.now()
        count = 0

        for eq_id in ids:
            assignment = project.assignments.filter(
                equipment_id=eq_id,
                checked_out_at__isnull=False,
                returned_at__isnull=True,
            ).first()
            if assignment:
                assignment.returned_at = now
                assignment.returned_by = request.user
                assignment.save()
                count += 1

        if count:
            messages.success(request, f"{count} 点を返却処理しました。")
        return redirect("return_scan", pk=project.pk)

    # --- GET: 出庫済みの機材を取得（返却済みは読み込み済み側に置く） ---
    assignments = project.assignments.filter(
        checked_out_at__isnull=False
    ).select_related(
        "equipment", "equipment__area", "equipment__kind", "equipment__storage_location"
    ).order_by("equipment__management_code")

    items = []
    done_codes = []
    for a in assignments:
        e = a.equipment
        items.append({
            "id": e.id,
            "code": e.management_code,
            "area": e.area.name,
            "model": e.model_name,
            "name": e.name,
        })
        if a.returned_at is not None:
            done_codes.append(e.management_code)

    # 未出庫の機材（スキャンされたときに専用の警告を出すため）
    not_shipped = list(
        project.assignments.filter(checked_out_at__isnull=True)
        .values_list("equipment__management_code", flat=True)
    )

    return render(request, "equipment/return_scan.html", {
        "project": project,
        "items_json": json.dumps(items, ensure_ascii=False),
        "done_json": json.dumps(done_codes, ensure_ascii=False),
        "not_shipped_json": json.dumps(not_shipped, ensure_ascii=False),
    })
    
@login_required
def next_code_api(request):
    """次の品目コードを計算して返す（新規機材登録のガイド用）"""
    area_id = request.GET.get("area", "")
    kind_id = request.GET.get("kind", "")
    mode = request.GET.get("mode", "")          # "new" or "existing"
    model_prefix = request.GET.get("model", "") # 既存の場合、先頭7桁

    area = Area.objects.filter(pk=area_id).first()
    kind = Kind.objects.filter(pk=kind_id).first()

    if not area or not kind:
        return JsonResponse({"error": "領域と種別を選択してください。"}, status=400)

    if not area.code:
        return JsonResponse({"error": f"領域「{area.name}」にコードが設定されていません。"}, status=400)

    prefix4 = f"{area.code}{kind.sort_order:02d}"

    if mode == "new":
        # 同じ領域・種別のモデル番号の最大値 + 1（削除済みは除く）
        codes = Equipment.objects.filter(
            area=area, kind=kind, is_deleted=False
        ).values_list("management_code", flat=True)

        max_model = 0
        for c in codes:
            if len(c) == 10 and c[4:7].isdigit():
                max_model = max(max_model, int(c[4:7]))

        new_code = f"{prefix4}{max_model + 1:03d}001"
        return JsonResponse({
            "code": new_code,
            "model_name": "",
            "name": "",
            "storage_location_id": None,
            "serial": 1,
        })

    if mode == "existing":
        if len(model_prefix) != 7:
            return JsonResponse({"error": "機種を選択してください。"}, status=400)

        siblings = Equipment.objects.filter(
            management_code__startswith=model_prefix, is_deleted=False
        ).order_by("management_code")

        if not siblings.exists():
            return JsonResponse({"error": "該当する機材が見つかりません。"}, status=400)

        max_serial = 0
        for c in siblings.values_list("management_code", flat=True):
            if len(c) == 10 and c[7:].isdigit():
                max_serial = max(max_serial, int(c[7:]))

        base = siblings.first()
        next_serial = max_serial + 1

        # 機種名の _No○ を差し替える
        import re
        base_model = re.sub(r"_No\d+$", "", base.model_name)
        new_model_name = f"{base_model}_No{next_serial}"

        return JsonResponse({
            "code": f"{model_prefix}{next_serial:03d}",
            "model_name": new_model_name,
            "name": base.name,
            "storage_location_id": base.storage_location_id,
            "serial": next_serial,
        })

    return JsonResponse({"error": "モードが不正です。"}, status=400)


@login_required
def model_list_api(request):
    """指定した領域・種別の機種一覧を返す（既存を増やす場合の選択肢）"""
    area_id = request.GET.get("area", "")
    kind_id = request.GET.get("kind", "")

    equipments = Equipment.objects.filter(
        area_id=area_id, kind_id=kind_id, is_deleted=False
    ).order_by("management_code")

    # 先頭7桁でまとめる
    import re
    seen = {}
    for e in equipments:
        prefix = e.management_code[:7]
        if prefix not in seen:
            base_model = re.sub(r"_No\d+$", "", e.model_name)
            seen[prefix] = {
                "prefix": prefix,
                "label": f"{base_model}（{prefix}）",
                "count": 0,
            }
        seen[prefix]["count"] += 1

    models = [
        {"prefix": v["prefix"], "label": f"{v['label']} {v['count']}台"}
        for v in seen.values()
    ]
    return JsonResponse({"models": models})