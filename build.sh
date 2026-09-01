#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

# 管理者アカウントを作成（環境変数が設定されている場合のみ）
python manage.py shell << EOF
from django.contrib.auth.models import User
import os

username = os.environ.get("ADMIN_USERNAME")
password = os.environ.get("ADMIN_PASSWORD")

if username and password:
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username=username, password=password)
        print(f"管理者 {username} を作成しました。")
    else:
        print(f"管理者 {username} は既に存在します。")
EOF