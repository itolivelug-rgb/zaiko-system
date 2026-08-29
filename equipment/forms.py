from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Equipment, Project


class UserCreateForm(UserCreationForm):
    """ユーザー新規作成フォーム"""

    class Meta:
        model = User
        fields = ("username", "last_name", "first_name", "email")
        labels = {
            "username": "ユーザー名（ログインID）",
            "last_name": "姓",
            "first_name": "名",
            "email": "メールアドレス",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "パスワード"
        self.fields["password2"].label = "パスワード（確認用）"
        self.fields["email"].required = False


class UserUpdateForm(forms.ModelForm):
    """ユーザー編集フォーム（パスワード以外）"""

    class Meta:
        model = User
        fields = ("last_name", "first_name", "email", "is_active", "is_staff")
        labels = {
            "last_name": "姓",
            "first_name": "名",
            "email": "メールアドレス",
            "is_active": "有効（チェックを外すとログインできなくなります）",
            "is_staff": "管理者権限（ユーザー管理・QR作成が可能になります）",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = False

class EquipmentForm(forms.ModelForm):
    """機材の登録・編集フォーム"""

    class Meta:
        model = Equipment
        fields = (
            "management_code",
            "area",
            "kind",
            "model_name",
            "name",
            "storage_location",
            "notes",
        )
        labels = {
            "management_code": "品目コード",
            "area": "領域",
            "kind": "種別",
            "model_name": "機種",
            "name": "名称",
            "storage_location": "保管場所",
            "notes": "備考",
        }
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        """領域と種別の組み合わせが正しいかチェックする"""
        cleaned_data = super().clean()
        area = cleaned_data.get("area")
        kind = cleaned_data.get("kind")

        if area and kind and kind.area_id != area.id:
            self.add_error("kind", "選択された領域に属さない種別です。")

        return cleaned_data        
    
class ProjectForm(forms.ModelForm):
    """案件の登録・編集フォーム"""

    class Meta:
        model = Project
        fields = ("name", "loan_date", "return_date", "notes")
        labels = {
            "name": "案件名",
            "loan_date": "貸出日",
            "return_date": "返却予定日",
            "notes": "備考",
        }
        widgets = {
            "loan_date": forms.DateInput(attrs={"type": "date"}),
            "return_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        loan_date = cleaned_data.get("loan_date")
        return_date = cleaned_data.get("return_date")

        today = timezone.localdate()

        # 新規登録時のみ過去日付を禁止（既存案件の編集では過去日付を許容する）
        if loan_date and not self.instance.pk and loan_date < today:
            self.add_error("loan_date", "貸出日に過去の日付は指定できません。")

        if loan_date and return_date and return_date < loan_date:
            self.add_error("return_date", "返却予定日は貸出日以降にしてください。")

        return cleaned_data