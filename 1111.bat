@echo off
cd /d %~dp0
git init
git add .
git commit -m "feat: �����˶�ʱ�Զ�������Ϣ"
git remote add origin https://github.com/xiaohui005/jiqiren.git
git push -u origin master
pause